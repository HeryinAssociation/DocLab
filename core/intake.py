"""Durable book intake, reusing Doclab's existing conversion and review modules."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

from .config import ROOT, load_config, work_dir
from .util import now_iso, read_json, write_json, write_text

INBOX = ROOT / 'Doclab-Inbox'
JOBS = INBOX / 'jobs'
CONFIG = INBOX / 'config' / 'intake.local.json'
STAGES = {'received', 'ocr_running', 'indexed', 'exporting'}
ARCHIVE_EXCLUDED_NAMES = {'完整书籍包.zip', 'AI核校报告.md', 'AI校验报告.md'}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def unique_relatives(paths):
    """给归档附件生成唯一显示名。

    同一个父条目下附件名必须唯一：EPUB 源书与 doclab 生成的整书 EPUB 可能同名
    （都叫《书名》.epub），Zotero 回执按 relative 索引，重名会把两份文件并成一条。
    只改附件的显示名，`path` 仍指向原文件，哈希核对不受影响。
    """
    used, out = set(), []
    for p in paths:
        p = Path(p)
        rel = p.name
        if rel in used:
            n = 2
            while f'{p.stem}（{n}）{p.suffix}' in used:
                n += 1
            rel = f'{p.stem}（{n}）{p.suffix}'
        used.add(rel)
        out.append((p, rel))
    return out


def inside(path, root):
    p, r = Path(path).resolve(), Path(root).resolve()
    if p != r and r not in p.parents:
        raise ValueError('路径超出允许目录')
    return p


def job_path(jid):
    if not re.fullmatch(r'[a-f0-9]{24}', str(jid)):
        raise ValueError('非法 job_id')
    return JOBS / jid / 'job.json'


@contextlib.contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        if not path.stat().st_size:
            f.write(b'0'); f.flush()
        f.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def load(jid):
    p = job_path(jid)
    if not p.is_file():
        raise FileNotFoundError('任务不存在')
    return json.loads(p.read_text(encoding='utf-8'))


def save(j, stage=None, **values):
    if stage and stage != j.get('stage'):
        j['stage'] = stage
        j['event_seq'] = j.get('event_seq', 0) + 1
    j.update(values)
    j['updated_at'] = now_iso()
    write_json(job_path(j['job_id']), j)
    return j


def summary(j):
    keys = ('job_id', 'source_id', 'stage', 'event_seq', 'revision', 'owner_session',
            'review_depth', 'export_depth', 'error', 'output', 'verification',
            'coverage', 'archive', 'unresolved', 'rounds', 'epub')
    out = {k: j[k] for k in keys if k in j}
    if isinstance(out.get('archive'), dict):
        ar = out['archive']
        out['archive'] = {k: ar[k] for k in ('plan_id', 'file_count', 'target', 'parent_key',
            'library_id', 'item_uri', 'verified', 'resource_sync_verified', 'note') if k in ar}
        if 'files' in ar:
            out['archive']['file_count'] = len(ar['files'])
    if isinstance(out.get('epub'), dict):
        ep = out['epub']
        out['epub'] = {k: ep[k] for k in ('name', 'path', 'bytes', 'chapters', 'images',
                                          'nav_entries', 'verified', 'warnings',
                                          'missing_images', 'unsupported_images')
                       if k in ep}
    out['ok'] = j['stage'] != 'failed'
    out['next_action'] = {
        'needs_ai_review': 'review_read → review_apply(complete=true) → finalize',
        'validated': 'archive_book(action=prepare)',
        'archive_prepared': 'archive_book(action=commit, plan_id=...)',
        'completed': '读取归档回执',
        'needs_review': '解决 unresolved 后重新核校',
        'failed': '查看 error，book_start(job_id=...) 恢复',
    }.get(j['stage'], '等待后台阶段完成，无需反复询问模型')
    return out


def start(a):
    JOBS.mkdir(parents=True, exist_ok=True)
    with locked(JOBS / '.registry.lock'):
        if a.get('job_id'):
            j = load(a['job_id'])
            if a.get('session_id'):
                if j.get('owner_session') not in (None, '', a['session_id']):
                    raise ValueError('任务已绑定其他会话')
                save(j, owner_session=a['session_id'])
        else:
            p = Path(a['file']).expanduser().resolve(strict=True)
            if p.suffix.lower() not in ('.pdf', '.epub') or not p.is_file():
                raise ValueError('只接收 PDF / EPUB 文件')
            sha = digest(p)
            rd, ed = int(a.get('review_depth', 3)), int(a.get('export_depth', 1))
            if not 1 <= rd <= 9 or not 0 <= ed <= rd:
                raise ValueError('要求 1≤核校深度≤9，0≤导出深度≤核校深度')
            jid = hashlib.sha256(f'{sha}:{rd}:{ed}:intake-v1'.encode()).hexdigest()[:24]
            if job_path(jid).exists():
                j = load(jid)
                if a.get('session_id') and j.get('owner_session') not in (None, '', a['session_id']):
                    return summary(j)
                if a.get('session_id'):
                    save(j, owner_session=a['session_id'])
            else:
                if p.suffix.lower() == '.pdf':
                    with p.open('rb') as f:
                        if b'%PDF-' not in f.read(1024):
                            raise ValueError('文件不是有效 PDF')
                elif not zipfile.is_zipfile(p):
                    raise ValueError('文件不是 EPUB ZIP')
                dest = INBOX / 'inbox' / sha[:16] / p.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if p != dest:
                    shutil.copy2(p, dest)
                if digest(dest) != sha:
                    raise ValueError('接收文件哈希不一致')
                j = {'job_id': jid, 'stage': 'received', 'event_seq': 1,
                     'created_at': now_iso(), 'file': str(dest), 'sha256': sha,
                     'owner_session': a.get('session_id', ''), 'review_depth': rd,
                     'export_depth': ed, 'rounds': 0}
                save(j)
        if j['stage'] in STAGES | {'failed'}:
            spawn(j)
        return summary(load(j['job_id']))


def spawn(j):
    log = job_path(j['job_id']).parent / 'worker.log'
    with log.open('ab') as f:
        kw = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS} if os.name == 'nt' else {'start_new_session': True}
        subprocess.Popen([sys.executable, str(ROOT / 'workflow.py'), '--worker', j['job_id']],
                         cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=f, stderr=f,
                         env={**os.environ, 'PYTHONUTF8': '1'}, **kw)


def blocks(j):
    from .project import load_shards, iter_blocks
    wd = work_dir(j['source_id'])
    shards = load_shards(wd)
    return wd, shards, iter_blocks(shards)


def revision(j):
    wd = work_dir(j['source_id'])
    h = hashlib.sha256()
    for name in ('project.json', 'manual_edits.json', 'outline.json', 'page_calibration.json'):
        p = wd / name
        h.update(name.encode()); h.update(p.read_bytes() if p.exists() else b'<absent>')
    # Parser / locator fixes invalidate exports made by an earlier implementation.
    for name in ('project.py', 'pagecal.py', 'exporter.py', 'epub_export.py'):
        h.update((ROOT / 'core' / name).read_bytes())
    return h.hexdigest()


def recompute(j, pagecal=True):
    from . import manual
    from .pagecal import calibrate, write_calibration, load_calibration
    from .outline import build_tree, write_outline
    wd, shards, bs = blocks(j)
    pj = read_json(wd / 'project.json', {})
    if pagecal:
        cal = calibrate(j['source_id'], bs, [s.to_dict() for s in shards], anchors=manual.anchors_for(wd))
        write_calibration(wd, cal)
    else:
        cal = load_calibration(wd)
    _, oc = build_tree(j['source_id'], bs, doc_title=pj.get('doc_title', ''), calib=cal,
                       manual_levels=manual.levels_for(wd), manual_deleted=manual.deleted_for(wd),
                       manual_added=manual.added_for(wd))
    write_outline(wd, oc)
    from .audit import run
    run(j['source_id'], wd, depth=j['review_depth'])


def worker(jid):
    try:
        with locked(job_path(jid).parent / '.worker.lock'):
            j = load(jid)
            if j['stage'] not in STAGES | {'failed'}:
                return
            save(j, 'ocr_running', error=None)
            from .ingest import ingest_local
            pj = ingest_local(Path(j['file']), load_config(), log=lambda x: print(x, flush=True))
            save(j, source_id=pj['source_id'])
            with locked(work_dir(j['source_id']) / '.intake.lock'):
                wd = work_dir(j['source_id'])
                if not (wd / 'outline.json').exists() or not (wd / 'page_calibration.json').exists():
                    recompute(j)
                save(j, 'needs_ai_review', revision=revision(j), seen=[], unresolved=[])
    except (BlockingIOError, OSError) as exc:
        if isinstance(exc, BlockingIOError) or getattr(exc, 'winerror', 0) in (33, 36):
            return
        j = load(jid); save(j, 'failed', error=str(exc))
    except Exception as exc:
        j = load(jid); save(j, 'failed', error=f'{type(exc).__name__}: {exc}')


def status(a):
    j = load(a['job_id'])
    if j['stage'] == 'archiving':
        # HTTP may time out after Zotero has persisted the final receipt.
        # Reconcile that durable result without replaying the write request.
        try:
            with locked(job_path(j['job_id']).parent / '.api.lock'):
                j = load(j['job_id'])
                if j['stage'] == 'archiving':
                    directory = job_path(j['job_id']).parent
                    p = read_json(directory / 'archive-plan.json', {})
                    r = read_json(directory / 'zotero-receipt.json', {})
                    if valid_receipt(j, p, r):
                        finish_archive(j, p, r)
        except BlockingIOError:
            pass
    return summary(j)


def valid_receipt(j, plan, receipt):
    if not (receipt.get('ok') is True and receipt.get('verified') is True
            and receipt.get('job_id') == j['job_id']
            and receipt.get('plan_id') == plan.get('plan_id')
            and receipt.get('plan_id') == j.get('archive', {}).get('plan_id')
            and receipt.get('library_id') == plan.get('target', {}).get('library_id')
            and re.fullmatch(r'[A-Z0-9]{8}', str(receipt.get('parent_key', '')))):
        return False
    expected_parent = plan.get('target', {}).get('parent_key')
    if expected_parent and receipt['parent_key'] != expected_parent:
        return False
    expected = {f['relative']: f for f in plan.get('files', [])}
    actual = receipt.get('files', [])
    if not expected or len(actual) != len(expected) or {f.get('relative') for f in actual} != set(expected):
        return False
    for f in actual:
        src = expected[f['relative']]
        if f.get('source_sha256') != src['sha256'] or not re.fullmatch(r'[a-f0-9]{64}', f.get('stored_sha256', '')):
            return False
        if src['role'] != 'index' and f['stored_sha256'] != src['sha256']:
            return False
    return True


def finish_archive(j, plan, receipt):
    if not valid_receipt(j, plan, receipt):
        raise ValueError('归档回执身份、附件清单或哈希不完整')
    write_json(INBOX / 'receipts' / (j['job_id'] + '.json'), receipt)
    save(j, 'completed', archive=receipt)


def review_read(a):
    from .tocio import export_toc
    j = load(a['job_id'])
    wd, shards, bs = blocks(j)
    kind = a.get('kind', 'outline')
    limit = max(1, min(60, int(a.get('limit', 40))))
    off = max(0, int(a.get('offset', 0)))
    if kind == 'outline':
        toc = export_toc(wd, j['source_id'], 100 if a.get('include_deep') else j['review_depth'])
        ns = toc['nodes'][off:off + limit]
        # Mark only content actually delivered, not the entire book.
        with locked(job_path(j['job_id']).parent / '.api.lock'):
            j = load(j['job_id'])
            rev = revision(j)
            seen = set(j.get('seen', [])) if j.get('revision') == rev else set()
            seen.update(n['key'] for n in ns)
            if rev != j.get('revision'):
                save(j, 'needs_ai_review', revision=rev, seen=sorted(seen), review_note='', unresolved=[])
            else:
                save(j, seen=sorted(seen))
        return {'ok': True, 'revision': rev, 'total': toc['node_count'],
                'next_offset': off + limit if off + limit < toc['node_count'] else None,
                'nodes': [{k: n[k] for k in ('key', 'level', 'title', 'gid', 'shard', 'page_idx', 'page', 'flags')} for n in ns]}
    if kind == 'issues':
        d = read_json(wd / '目录校核.json', {})
        return {'ok': True, 'report': d, 'revision': revision(j)} if a.get('detail') else {
            'ok': True, 'verdict': d.get('verdict'), 'severity': d.get('severity'),
            'findings': (d.get('findings') or [])[off:off + limit],
            'total': len(d.get('findings') or []), 'revision': revision(j)}
    if kind == 'calibration':
        d = read_json(wd / 'page_calibration.json', {})
        key = a.get('part', 'segments')
        if key not in ('segments', 'observations', 'gaps', 'header_conflicts', 'continuity'):
            raise ValueError('不支持的校准字段')
        val = d.get(key, [])
        return {'ok': True, 'locator_type': d.get('locator_type'), 'verdict': d.get('verdict'),
                'part': key, 'data': val[off:off + limit] if isinstance(val, list) else val,
                'total': len(val), 'revision': revision(j)}
    if kind == 'blocks':
        q = re.sub(r'\s+', '', a.get('q', ''))
        lo, hi = int(a.get('gid_start', 0)), int(a.get('gid_end', len(bs) - 1))
        selected = [b for b in bs if lo <= b.gid <= hi and
                    (not q or q in re.sub(r'\s+', '', b.text or b.table_body or ''))]
        rows = [{'gid': b.gid, 'shard': b.shard, 'page_idx': b.page_idx, 'type': b.type,
                 'text': b.text or b.table_body or '', 'is_heading': b.is_heading} for b in selected[off:off + limit]]
        # Bound payload while preserving explicit continuation for long blocks.
        for row in rows:
            txt = row['text']; pos = max(0, int(a.get('text_offset', 0)))
            row.update(text=txt[pos:pos + 6000], full_len=len(txt),
                       next_text_offset=pos + 6000 if len(txt) > pos + 6000 else None)
        return {'ok': True, 'rows': rows, 'total': len(selected),
                'next_offset': off + limit if off + limit < len(selected) else None}
    if kind == 'page':
        sh = next(s for s in shards if s.tag == a['shard'])
        idx = int(a['page_idx'])
        if not 0 <= idx < sh.n_pages:
            raise ValueError('page_idx 超出分片范围')
        pdf = sh.pdf
        if not pdf:
            pj = read_json(wd / 'project.json', {})
            pdf = Path(pj.get('source_file', ''))
            if pdf.suffix.lower() != '.pdf' or not pdf.is_file():
                raise ValueError('此源没有可查看的 PDF 原页')
            idx += sum(s.n_pages for s in shards[:shards.index(sh)])
        sys.path.insert(0, str(ROOT / '.runtime'))
        import fitz
        dst = job_path(j['job_id']).parent / f'page-{a["shard"]}-{a["page_idx"]}.png'
        with fitz.open(str(pdf)) as doc:
            pix = doc[idx].get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
            pix.save(str(dst))
        return {'ok': True, '_image': str(dst), 'shard': a['shard'], 'page_idx': a['page_idx']}
    raise ValueError('未知 review kind')


def review_apply(a):
    from .tocio import export_toc, diff_toc, import_toc
    from .fixplan import validate, apply
    j = load(a['job_id'])
    with locked(job_path(j['job_id']).parent / '.api.lock'), locked(work_dir(j['source_id']) / '.intake.lock'):
        j = load(j['job_id'])
        if j['stage'] not in ('needs_ai_review', 'needs_review'):
            raise ValueError('当前阶段不允许核校修改')
        if a['base_revision'] != revision(j):
            raise ValueError('版本已变，请重新读取目录')
        if j.get('rounds', 0) >= 2 and a.get('operations'):
            raise ValueError('已达两轮自动修正上限，请人工处理剩余问题')
        wd = work_dir(j['source_id'])
        # Diff the complete snapshot so a deep node can be promoted without
        # interpreting the nodes omitted by the review view as deletions.
        toc = export_toc(wd, j['source_id'], 100)
        anchors, ops = [], a.get('operations') or []
        for op in ops:
            if not str(op.get('evidence', '')).strip():
                raise ValueError('每个改动须有具体 evidence')
            act = op['action']
            if act in ('anchor', 'anchor-clear'):
                if not str(op.get('scope_evidence', '')).strip():
                    raise ValueError('页码修改须说明整个覆盖区间的依据 scope_evidence')
                anchors.append(op); continue
            node = next((n for n in toc['nodes'] if n['key'] == str(op.get('key', ''))), None)
            if act in ('level', 'delete') and node is None:
                raise ValueError('key 不在当前核校范围内')
            if act == 'level':
                node['level'] = op['level']; node['reason'] = op['evidence']
            elif act == 'delete':
                toc['nodes'].remove(node); toc.setdefault('reasons', {})[node['key']] = op['evidence']
            elif act == 'add':
                toc['nodes'].append({'key': '', 'gid': op['gid'], 'title': op['title'],
                                     'level': op['level'], 'offset': op.get('offset', 0), 'reason': op['evidence']})
            else:
                raise ValueError('未知操作')
        d = diff_toc(wd, toc)
        if d['errors']:
            return {'ok': False, 'errors': d['errors']}
        if anchors:
            _, shards, bs = blocks(j)
            for op in anchors:
                s = next((s for s in shards if s.tag == op['shard']), None)
                if not s or not 0 <= int(op['page_idx']) < s.n_pages:
                    raise ValueError('锚点超出分片范围')
            validate({'items': anchors}, wd)
        if not a.get('apply', False):
            return {'ok': True, 'dry_run': True, 'changes': d['changes'], 'anchors': anchors,
                    'warnings': d['warnings'], 'revision': revision(j)}
        if a.get('complete') and not str(a.get('review_note', '')).strip():
            raise ValueError('完成核校需要 review_note 与实际检查范围')
        backup = job_path(j['job_id']).parent / f'backup-{uuid.uuid4().hex[:12]}'
        backup.mkdir()
        names = ('manual_edits.json', 'outline.json', 'page_calibration.json')
        present = []
        for n in names:
            if (wd / n).exists():
                shutil.copy2(wd / n, backup / n); present.append(n)
        try:
            if anchors:
                r = apply({'name': 'AI 页码核校', 'items': anchors}, wd, j['source_id'])
                if r['failed']:
                    raise ValueError(str(r['failed']))
                recompute(j)
                if d['items']:
                    # Invalidate old directory diff: page changes may alter synthetic keys.
                    save(j, 'needs_ai_review', revision=revision(j), seen=[], rounds=j['rounds'] + 1)
                    return {'ok': True, 'anchors_applied': len(anchors), 'next_action': '重读目录后再提交目录变更', 'revision': j['revision']}
            elif d['items']:
                r = import_toc(wd, j['source_id'], toc, do_apply=True)
                if not r['ok'] or r.get('failed'):
                    raise ValueError(str(r))
                recompute(j, False)
        except Exception:
            for n in names:
                if n in present:
                    shutil.copy2(backup / n, wd / n)
                else:
                    (wd / n).unlink(missing_ok=True)
            raise
        changed = bool(anchors or d['items'])
        unresolved = a.get('unresolved') or []
        rev = revision(j)
        state = 'needs_review' if unresolved else 'needs_ai_review'
        values = {'revision': rev, 'backup': str(backup), 'unresolved': unresolved,
                  'rounds': j['rounds'] + int(changed)}
        if changed:
            values['seen'] = []
        if a.get('complete') and not changed and not unresolved:
            all_keys = {n['key'] for n in toc['nodes'] if n['level'] <= j['review_depth']}
            if not all_keys.issubset(set(j.get('seen', []))):
                raise ValueError('尚未读取核校范围内的全部目录节点')
            state = 'reviewed'; values['review_note'] = a['review_note']
        save(j, state, **values)
        return summary(j)


def full_coverage(bs, index, out):
    from .project import CONTENT_TYPES, NOTE_TYPES, NOISE_TYPES, is_noise_text
    from .verify import norm
    by = {}
    for f in index['files']:
        for b in f['blocks']:
            by.setdefault(b['gid'], []).append(b.get('text', ''))
    missing, unknown, notes = [], [], []
    text = '\n'.join(Path(out, f['path']).read_text(encoding='utf-8') for f in index['files'])
    for b in bs:
        if b.type in NOISE_TYPES or is_noise_text(b.text):
            continue
        if b.type not in CONTENT_TYPES | NOTE_TYPES:
            unknown.append({'gid': b.gid, 'type': b.type}); continue
        raw = norm(b.text or b.table_body or '')
        if b.type in NOTE_TYPES:
            if raw and raw not in norm(text):
                notes.append(b.gid)
        elif b.gid not in by or (raw and norm(''.join(by[b.gid])) != raw):
            missing.append(b.gid)
    missing_images = [b.gid for b in bs if b.type in ('image', 'chart') and b.img_path
                      and not (Path(out) / 'images' / Path(b.img_path).name).is_file()]
    return {'status': 'fail' if missing or unknown or notes or missing_images else 'pass',
            'missing_gids': missing[:100], 'missing_count': len(missing),
            'unclassified': unknown[:30], 'missing_footnotes': notes[:100],
            'missing_images': missing_images[:100]}


def finalize(a):
    from .exporter import export
    from .pagecal import load_calibration
    from .verify import run_all, verify_report_md
    j = load(a['job_id'])
    with locked(job_path(j['job_id']).parent / '.api.lock'), locked(work_dir(j['source_id']) / '.intake.lock'):
        j = load(j['job_id'])
        if j['stage'] not in ('reviewed', 'validated') or revision(j) != a['base_revision']:
            raise ValueError('需要已完成核校且未变化的版本')
        wd, shards, bs = blocks(j)
        od = job_path(j['job_id']).parent / 'exports' / f'{a["base_revision"][:16]}-L{j["export_depth"]}'
        od.mkdir(parents=True, exist_ok=True)
        cal = load_calibration(wd)
        pj = read_json(wd / 'project.json', {})
        mode = (pj.get('defaults') or {}).get('footnotes', 'page-end')
        # EPUB 是 md 的同源装帧：export 内部用刚写出的分节 md 装订，失败即抛出。
        meta = export(j['source_id'], wd, od, bs, cal, read_json(wd / 'outline.json'),
                      j['export_depth'], footnote_mode=mode,
                      shards_meta=[s.to_dict() for s in shards])
        ep = meta.get('epub') or {}
        if not ep or not (ep.get('verify') or {}).get('ok'):
            raise ValueError('EPUB 未生成或结构自检未通过，不能进入归档')
        report = run_all(wd, od, bs, cal)
        cov = full_coverage(bs, read_json(wd / 'export_index.json'), od)
        # EPUB 里被置为占位的缺图/不收格式也算未完成：不能因为 md 侧漏检就交付残书。
        missing_epub_images = (list(ep.get('missing_images') or [])
                               + list(ep.get('unsupported_images') or []))
        if missing_epub_images:
            cov['status'] = 'fail'
            cov['missing_images'] = sorted(set(cov.get('missing_images') or [])
                                           | set(missing_epub_images))
            cov['note'] = 'EPUB 有图片未装进包（缺文件或格式不受支持），已置占位'
        write_text(od / '校验报告.md', verify_report_md(report, j['source_id']))
        write_text(od / 'AI核校报告.md', '# AI 核校报告\n\n' + j.get('review_note', '') + '\n\n' + json.dumps(cov, ensure_ascii=False, indent=2))
        write_json(od / 'coverage.json', cov)
        result_stage = 'validated' if report['verdict'] != 'fail' and cov['status'] == 'pass' else 'needs_review'
        save(j, result_stage, output=str(od), verification=report['verdict'], coverage=cov,
             epub={'name': ep.get('name'), 'path': ep.get('path'), 'bytes': ep.get('bytes'),
                   'sha256': ep.get('sha256'), 'chapters': ep.get('chapters'),
                   'images': ep.get('images'), 'cover': ep.get('cover'),
                   'nav_entries': ep.get('nav_entries'),
                   'warnings': ep.get('warnings') or [],
                   'missing_images': missing_epub_images,
                   'unsupported_images': ep.get('unsupported_images') or [],
                   'verified': True},
             revision=revision(j))
        return summary(j)


def zotero(action, data=None):
    cfg = read_json(CONFIG, {})
    if not cfg.get('bridge_token'):
        raise ValueError('Zotero 归档插件尚未配置')
    body = json.dumps({'action': action, **(data or {})}, ensure_ascii=False).encode()
    url = 'http://127.0.0.1:23119/doclab/archive'
    req = urllib.request.Request(url, body, {'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + cfg['bridge_token']})
    with urllib.request.urlopen(req, timeout=600 if action == 'commit' else 120) as r:
        result = json.load(r)
    if not result.get('ok'):
        raise ValueError(result.get('message', 'Zotero 操作失败'))
    return result


def archive(a):
    j = load(a['job_id'])
    with locked(job_path(j['job_id']).parent / '.api.lock'):
        j = load(j['job_id'])
        if a.get('action', 'prepare') == 'prepare':
            if j['stage'] not in ('validated', 'archive_prepared'):
                raise ValueError('归档前需通过核校和导出验证')
            if revision(j) != j['revision']:
                raise ValueError('核校版本已变，需重新导出验证')
            cfg = read_json(CONFIG, {})
            target = {'library_id': a.get('library_id') or cfg.get('library_id'),
                      'collection_key': a.get('collection_key') or cfg.get('collection_key'),
                      'parent_key': a.get('parent_key'), 'metadata': a.get('metadata') or {},
                      'metadata_evidence': a.get('metadata_evidence', '')}
            if not target['parent_key'] and (not target['metadata'].get('title') or not target['metadata_evidence']):
                raise ValueError('新建条目须有核实的题名和 metadata_evidence；不能猜题录')
            check = zotero('prepare', target)
            od = inside(j['output'], job_path(j['job_id']).parent)
            epubs = sorted(p for p in od.glob('*.epub') if p.is_file())
            if len(epubs) != 1:
                raise ValueError(f'导出目录应有 1 个 EPUB，实际 {len(epubs)} 个；请重新 finalize')
            recorded = j.get('epub') or {}
            if recorded.get('name') and recorded['name'] != epubs[0].name:
                raise ValueError('EPUB 与任务记录不一致；请重新 finalize')
            if recorded.get('sha256') and digest(epubs[0]) != recorded['sha256']:
                raise ValueError('EPUB 已变化；请重新 finalize')
            paths = [Path(j['file']), *epubs,
                     *sorted(p for p in od.glob('*.md') if p.name not in ARCHIVE_EXCLUDED_NAMES),
                     *sorted(od.glob('*.json')), *sorted(od.glob('*.csv'))]
            files = [{'path': str(p), 'relative': rel, 'sha256': digest(p),
                      'role': ('index' if rel == '00-目录.md'
                               else 'epub' if p.suffix.lower() == '.epub' else 'file')}
                     for p, rel in unique_relatives(paths)]
            assets = [{'path': str(p), 'relative': p.relative_to(od).as_posix(), 'sha256': digest(p)}
                      for p in sorted((od / 'images').rglob('*')) if p.is_file()] if (od / 'images').is_dir() else []
            plan = {'job_id': j['job_id'], 'target': check['target'], 'revision': j['revision'],
                    'files': files, 'assets': assets}
            plan['plan_id'] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            write_json(job_path(j['job_id']).parent / 'archive-plan.json', plan)
            save(j, 'archive_prepared', archive={'plan_id': plan['plan_id'], 'file_count': len(files), 'target': check['target']})
            return summary(j)
        if a['action'] == 'commit':
            if j['stage'] == 'completed':
                return summary(j)
            if j['stage'] not in ('archive_prepared', 'archiving'):
                raise ValueError('请先 prepare')
            p = read_json(job_path(j['job_id']).parent / 'archive-plan.json')
            if not p or a['plan_id'] != p['plan_id'] or revision(j) != p['revision']:
                raise ValueError('归档计划或源版本已变化')
            if any(Path(f['relative']).name in ARCHIVE_EXCLUDED_NAMES for f in p['files']):
                raise ValueError('旧计划含已排除的完整压缩包或 AI 核校报告，请重新 prepare')
            if not any(str(f.get('relative', '')).lower().endswith('.epub')
                       for f in p['files']):
                raise ValueError('旧计划没有 EPUB，请重新 prepare')
            receipt = read_json(job_path(j['job_id']).parent / 'zotero-receipt.json', {})
            if valid_receipt(j, p, receipt):
                finish_archive(j, p, receipt)
                return summary(j)
            for f in p['files'] + p['assets']:
                if digest(f['path']) != f['sha256']:
                    raise ValueError('待归档文件已变化，请重新生成计划')
            save(j, 'archiving')
            r = zotero('commit', {'plan': p})
            if not r.get('verified'):
                raise ValueError('Zotero 归档未通过回读核验')
            finish_archive(j, p, r)
            return summary(j)
        raise ValueError('未知归档动作')


def dispatch(name, a):
    return {'doclab_book_start': start, 'doclab_job_status': status,
            'doclab_review_read': review_read, 'doclab_review_apply': review_apply,
            'doclab_book_finalize': finalize, 'doclab_archive_book': archive}[name](a)
