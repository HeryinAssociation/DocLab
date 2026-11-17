"""Isolated intake regressions: no OCR service, real books, or Zotero writes."""
from __future__ import annotations
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from core import intake
from core.util import write_json

class IntakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='intake-')
        self.root=Path(self.tmp.name);self.wd=self.root/'source';self.wd.mkdir()
        self.patches=[patch.object(intake,'INBOX',self.root),patch.object(intake,'JOBS',self.root/'jobs'),
                      patch.object(intake,'work_dir',lambda sid:self.wd)]
        for p in self.patches:p.start()
        raw=[{'type':'text','text':'第一章 总论','text_level':1,'page_idx':0,'_trusted':True},
             {'type':'text','text':'父章的独立导语不能丢失。','page_idx':0},
             {'type':'text','text':'第一节 范围','text_level':2,'page_idx':1,'_trusted':True},
             {'type':'text','text':'第一节正文。','page_idx':1},
             {'type':'page_footnote','text':'这是需要保留的页下注。','page_idx':1},
             {'type':'text','text':'第二节 方法','text_level':2,'page_idx':2,'_trusted':True},
             {'type':'text','text':'第二节正文。','page_idx':2}]
        write_json(self.wd/'content_list.json',raw)
        write_json(self.wd/'project.json',{'source_id':'fixture','doc_title':'测试书',
          'source_file':str(self.wd/'source.epub'),'defaults':{'footnotes':'inline'},'shards':[
          {'tag':'P1','project_dir':str(self.wd),'n_pages':3}]})
        self.j={'job_id':'a'*24,'stage':'needs_ai_review','event_seq':1,'source_id':'fixture',
                'review_depth':3,'export_depth':2,'rounds':0,'seen':[]}
        intake.recompute(self.j);self.j['revision']=intake.revision(self.j);intake.save(self.j)

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def complete(self):
        intake.review_read({'job_id':self.j['job_id']})
        return intake.review_apply({'job_id':self.j['job_id'],'base_revision':self.j['revision'],
          'apply':True,'complete':True,'review_note':'已读全部目录，并对照测试正文块；未做原页视觉检查'})

    def test_unread_nodes_cannot_complete(self):
        with self.assertRaisesRegex(ValueError,'全部目录'):
            intake.review_apply({'job_id':self.j['job_id'],'base_revision':self.j['revision'],
              'apply':True,'complete':True,'review_note':'声称已检查'})

    def test_stale_revision_rejected(self):
        with self.assertRaisesRegex(ValueError,'版本'):
            intake.review_apply({'job_id':self.j['job_id'],'base_revision':'stale','apply':True})

    def test_full_flow_preserves_parent_intro_and_footnote(self):
        self.assertEqual(self.complete()['stage'],'reviewed')
        r=intake.finalize({'job_id':self.j['job_id'],'base_revision':self.j['revision']})
        self.assertEqual(r['stage'],'validated',r)
        self.assertEqual(r['coverage']['status'],'pass')
        text='\n'.join(p.read_text(encoding='utf8') for p in Path(r['output']).glob('*.md'))
        self.assertIn('父章的独立导语不能丢失',text)
        self.assertIn('这是需要保留的页下注',text)
        # 导出同时装订 EPUB：文件真实存在、结构自检通过、章数与导出记录一致
        self.assertTrue(r['epub']['verified'], r['epub'])
        ep=Path(r['epub']['path']); self.assertTrue(ep.is_file())
        self.assertEqual(ep.suffix,'.epub'); self.assertGreater(ep.stat().st_size,0)
        self.assertGreaterEqual(r['epub']['chapters'],1)
        self.assertFalse(r['epub']['missing_images'])

    def test_dry_run_does_not_change_manual_layer(self):
        toc=intake.review_read({'job_id':self.j['job_id']})
        node=next(n for n in toc['nodes'] if n['level']>0)
        r=intake.review_apply({'job_id':self.j['job_id'],'base_revision':toc['revision'],
          'operations':[{'action':'level','key':node['key'],'level':2,'evidence':'fixture 原文同层'}]})
        self.assertTrue(r['dry_run']);self.assertFalse((self.wd/'manual_edits.json').exists())

    def test_missing_source_content_fails_independent_coverage(self):
        _,_,bs=intake.blocks(self.j)
        out=self.root/'fake';out.mkdir();(out/'one.md').write_text('第一章 总论',encoding='utf8')
        r=intake.full_coverage(bs,{'files':[{'path':'one.md','blocks':[{'gid':0,'text':'第一章 总论'}]}]},out)
        self.assertEqual(r['status'],'fail');self.assertGreater(r['missing_count'],0)

    def test_anchor_requires_scope_evidence(self):
        with self.assertRaisesRegex(ValueError,'scope_evidence'):
            intake.review_apply({'job_id':self.j['job_id'],'base_revision':self.j['revision'],
              'operations':[{'action':'anchor','shard':'P1','page_idx':0,'printed':1,'evidence':'孤立一页'}]})

    def test_path_guard(self):
        with self.assertRaises(ValueError):intake.inside(self.root.parent/'unrelated',self.root)

    def test_new_intake_defaults_to_l1_export(self):
        pdf=self.root/'new.pdf';pdf.write_bytes(b'%PDF-1.4\nfixture')
        with patch.object(intake,'spawn') as spawn:
            r=intake.start({'file':str(pdf)})
        self.assertEqual(r['review_depth'],3);self.assertEqual(r['export_depth'],1)
        spawn.assert_called_once()

    def test_l1_archive_excludes_bundle_and_ai_report(self):
        from core.util import read_json
        original=self.wd/'source.pdf';original.write_bytes(b'%PDF-1.4\nfixture')
        intake.save(self.j,file=str(original),export_depth=1)
        self.complete()
        r=intake.finalize({'job_id':self.j['job_id'],'base_revision':self.j['revision']})
        self.assertEqual(r['stage'],'validated');out=Path(r['output'])
        self.assertTrue(out.name.endswith('-L1'))
        self.assertTrue((out/'AI核校报告.md').is_file())
        (out/'AI校验报告.md').write_text('本地报告',encoding='utf8')
        with patch.object(intake,'zotero',return_value={'ok':True,'target':{
                'library_id':1,'collection_key':'BOOKS','parent_key':None}}):
            intake.archive({'job_id':self.j['job_id'],'action':'prepare',
                'metadata':{'title':'测试书'},'metadata_evidence':'fixture'})
        planpath=intake.job_path(self.j['job_id']).parent/'archive-plan.json'
        plan=read_json(planpath);names={f['relative'] for f in plan['files']}
        self.assertFalse(names & intake.ARCHIVE_EXCLUDED_NAMES)
        self.assertIn('source.pdf',names);self.assertIn('00-目录.md',names)
        self.assertIn('校验报告.md',names)
        # EPUB 与 md 一起归档，且被标成独立 role 供回执核对
        epubs=[f for f in plan['files'] if f['relative'].endswith('.epub')]
        self.assertEqual(len(epubs),1);self.assertEqual(epubs[0]['role'],'epub')
        self.assertTrue(Path(epubs[0]['path']).is_file())
        self.assertFalse((out.parent/'完整书籍包.zip').exists())
        # A plan prepared before the preference change must be regenerated too.
        plan['files'].append({'relative':'AI核校报告.md'})
        write_json(planpath,plan)
        with patch.object(intake,'zotero') as zotero:
            with self.assertRaisesRegex(ValueError,'旧计划'):
                intake.archive({'job_id':self.j['job_id'],'action':'commit','plan_id':plan['plan_id']})
            zotero.assert_not_called()

    def test_archive_refuses_when_epub_is_missing_or_changed(self):
        original=self.wd/'source.pdf';original.write_bytes(b'%PDF-1.4\nfixture')
        intake.save(self.j,file=str(original),export_depth=1)
        self.complete()
        r=intake.finalize({'job_id':self.j['job_id'],'base_revision':self.j['revision']})
        ep=Path(r['epub']['path'])
        with patch.object(intake,'zotero',return_value={'ok':True,'target':{
                'library_id':1,'collection_key':'BOOKS','parent_key':None}}):
            # 内容变化：哈希与 finalize 记录不一致，必须重做导出
            ep.write_bytes(ep.read_bytes()+b'x')
            with self.assertRaisesRegex(ValueError,'EPUB 已变化'):
                intake.archive({'job_id':self.j['job_id'],'action':'prepare',
                    'metadata':{'title':'测试书'},'metadata_evidence':'fixture'})
            # 文件缺失：不能生成一个漏了 EPUB 的归档计划
            ep.unlink()
            with self.assertRaisesRegex(ValueError,'EPUB'):
                intake.archive({'job_id':self.j['job_id'],'action':'prepare',
                    'metadata':{'title':'测试书'},'metadata_evidence':'fixture'})

    def test_duplicate_relative_names_are_disambiguated(self):
        pairs=intake.unique_relatives([Path('a')/'书.epub',Path('b')/'书.epub',Path('c')/'书.epub'])
        self.assertEqual([r for _,r in pairs],['书.epub','书（2）.epub','书（3）.epub'])

    def test_old_plan_without_epub_cannot_commit(self):
        from core.util import read_json
        original=self.wd/'source.pdf';original.write_bytes(b'%PDF-1.4\nfixture')
        intake.save(self.j,file=str(original),export_depth=1)
        self.complete()
        intake.finalize({'job_id':self.j['job_id'],'base_revision':self.j['revision']})
        with patch.object(intake,'zotero',return_value={'ok':True,'target':{
                'library_id':1,'collection_key':'BOOKS','parent_key':None}}):
            intake.archive({'job_id':self.j['job_id'],'action':'prepare',
                'metadata':{'title':'测试书'},'metadata_evidence':'fixture'})
        p=intake.job_path(self.j['job_id']).parent/'archive-plan.json'
        plan=read_json(p)
        plan['files']=[f for f in plan['files'] if not f['relative'].endswith('.epub')]
        write_json(p,plan)
        with patch.object(intake,'zotero') as zotero:
            with self.assertRaisesRegex(ValueError,'没有 EPUB'):
                intake.archive({'job_id':self.j['job_id'],'action':'commit',
                                'plan_id':plan['plan_id']})
            zotero.assert_not_called()

    def test_mineru_list_entries_reach_markdown_and_coverage(self):
        from core.util import read_json
        p=self.wd/'content_list.json';raw=read_json(p)
        raw.append({'type':'list','page_idx':2,'list_items':['[1] 第一篇参考文献。','[2] 第二篇参考文献。']})
        write_json(p,raw);intake.recompute(self.j)
        self.j['revision']=intake.revision(self.j);intake.save(self.j)
        self.complete()
        r=intake.finalize({'job_id':self.j['job_id'],'base_revision':self.j['revision']})
        self.assertEqual(r['coverage']['status'],'pass')
        out=Path(r['output'])
        text='\n'.join(p.read_text(encoding='utf8') for p in out.glob('*.md'))
        self.assertIn('[1] 第一篇参考文献。',text);self.assertIn('[2] 第二篇参考文献。',text)
        _,_,bs=intake.blocks(self.j)
        index=read_json(self.wd/'export_index.json')
        for f in index['files']:
            f['blocks']=[b for b in f['blocks'] if b['gid']!=7]
        self.assertIn(7,intake.full_coverage(bs,index,out)['missing_gids'])

    def test_deep_heading_promotion_keeps_other_nodes(self):
        from core.util import read_json
        from core.tocio import export_toc
        oc=read_json(self.wd/'outline.json')
        node=oc['tree'][0]
        while node.get('children'):node=node['children'][-1]
        node['level']=10;key=str(node['key'])
        write_json(self.wd/'outline.json',oc)
        toc=intake.review_read({'job_id':self.j['job_id'],'include_deep':True})
        self.assertTrue(any(n['key']==key and n['level']==10 for n in toc['nodes']))
        a={'job_id':self.j['job_id'],'base_revision':toc['revision'],'operations':[
           {'action':'level','key':key,'level':2,'evidence':'原页同级类目'}]}
        dry=intake.review_apply(a);self.assertTrue(dry['ok'],dry)
        self.assertEqual(len(dry['changes']),1)
        intake.review_apply(dict(a,apply=True))
        self.assertEqual(len(export_toc(self.wd,'fixture',100)['nodes']),len(toc['nodes']))

    def test_repeated_front_numbers_have_distinct_locators(self):
        from core.pagecal import Calibration
        d={'segments':[{'shard':'P1','kind':'front','page_idx_start':start,'page_idx_end':start+1,
          'printed_start':1,'printed_end':2,'offset':1-start,'n_obs':2,'span':2,
          'coverage':1,'confidence':'medium'} for start in (4,6)]}
        c=Calibration.from_dict(d)
        self.assertNotEqual(c.locator('P1',4),c.locator('P1',6))
        from core.audit import parse_locator
        self.assertEqual(parse_locator(c.locator('P1',4)),(1,1))

    def test_timed_out_archive_recovers_from_complete_receipt_without_request(self):
        import hashlib
        h=hashlib.sha256(b'fixture').hexdigest()
        plan={'plan_id':'b'*64,'target':{'library_id':1},'files':[
            {'relative':'one.md','sha256':h,'role':'file'}]}
        rec={'ok':True,'verified':True,'job_id':self.j['job_id'],'plan_id':plan['plan_id'],
             'parent_key':'ABCDEFGH','library_id':1,'files':[
             {'relative':'one.md','source_sha256':h,'stored_sha256':h}]}
        intake.save(self.j,'archiving',archive={'plan_id':plan['plan_id']})
        d=intake.job_path(self.j['job_id']).parent
        write_json(d/'archive-plan.json',plan);write_json(d/'zotero-receipt.json',rec)
        with patch.object(intake,'zotero',side_effect=AssertionError('Must not replay HTTP')):
            r=intake.status({'job_id':self.j['job_id']})
        self.assertEqual(r['stage'],'completed');self.assertTrue(r['archive']['verified'])
        self.assertTrue((self.root/'receipts'/f"{self.j['job_id']}.json").is_file())

    def test_incomplete_or_wrong_archive_receipt_cannot_complete(self):
        j=dict(self.j,archive={'plan_id':'b'*64})
        plan={'plan_id':'b'*64,'target':{'library_id':1},'files':[
            {'relative':'one.md','sha256':'c'*64,'role':'file'}]}
        rec={'ok':True,'verified':True,'job_id':j['job_id'],'plan_id':'b'*64,
             'parent_key':'ABCDEFGH','library_id':1,'files':[]}
        self.assertFalse(intake.valid_receipt(j,plan,rec))
        rec['files']=[{'relative':'one.md','source_sha256':'d'*64,'stored_sha256':'c'*64}]
        self.assertFalse(intake.valid_receipt(j,plan,rec))

if __name__=='__main__':unittest.main(verbosity=2)
