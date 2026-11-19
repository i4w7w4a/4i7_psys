import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import context_pack as cp
import validate_runtime as vr

class CurrentContextTests(unittest.TestCase):
    def setUp(self):
        self.m=json.loads((ROOT/cp.MATRIX).read_text())
        self.prose=(ROOT/cp.DOC).read_text()
    def run_cli(self,*args):
        return subprocess.run([sys.executable,str(ROOT/'scripts/context_pack.py'),*args],capture_output=True,text=True)
    def test_full_56_and_no_silent_limit(self):
        p=cp.build_current(ROOT,[])
        self.assertEqual(len(p['focused_aspects']),56)
        self.assertEqual(p['context_aspects_included'],56)
        self.assertEqual(p['pillars'],self.m['pillars'])
        self.assertEqual(p['knowledge_records'],[])
    def test_p2_voice_p3_skeleton_end_to_end(self):
        for node,title in [('P2','Голос'),('P3','Скелет связей')]:
            r=self.run_cli('--node',node,'--format','json')
            self.assertEqual(r.returncode,0,r.stderr)
            p=json.loads(r.stdout)
            self.assertEqual(p['pillars'][0]['title'],title)
            self.assertEqual(len(p['focused_aspects']),8)
            self.assertNotIn('relations',p)
    def test_side_and_aspect_focus(self):
        self.assertEqual(cp.build_current(ROOT,['P3.2'])['focused_aspects'],['P3.2.1','P3.2.2'])
        self.assertEqual(cp.build_current(ROOT,['P3.2.1'])['focused_aspects'],['P3.2.1'])
        self.assertEqual(len(cp.build_current(ROOT,['i'])['focused_aspects']),56)
    def test_reopened_aspects_preserve_notes_and_empty_values(self):
        # Model an earlier open pair independently of the current editorial snapshot.
        side = self.m['pillars'][6]['sides'][3]
        side['title'] = None
        side['status'] = 'open'
        side['aspects'] = {
            key: {'status': 'open', 'text': None, 'source': None, 'origin': None}
            for key in ('yes', 'no')
        }
        side['draft_notes'] = {'status': 'incomplete_draft', 'note': 'Требует уточнения'}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in cp.SOURCE_PATHS:
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if name == cp.MATRIX:
                    dest.write_text(json.dumps(self.m), encoding='utf-8')
                else:
                    shutil.copyfile(ROOT / name, dest)
            pack = cp.build_current(root, ['P7.4'])

        self.assertEqual(pack['pillars'], [self.m['pillars'][6]])
        self.assertEqual(pack['focused_aspects'], ['P7.4.1', 'P7.4.2'])
        self.assertIn('draft_notes', cp.render(pack))

    def test_confirmed_pairs_in_current_pack(self):
        addresses = ['P1.3.1', 'P1.3.2', 'P7.4.1', 'P7.4.2']
        pack = cp.build_current(ROOT, addresses)
        self.assertEqual(pack['focused_aspects'], addresses)
        self.assertEqual(pack['pillars'], [self.m['pillars'][0], self.m['pillars'][6]])
        for pillar, side_index in zip(pack['pillars'], (2, 3)):
            for aspect in pillar['sides'][side_index]['aspects'].values():
                self.assertEqual(aspect['status'], 'captured')
                self.assertTrue(aspect['text'])
                self.assertIn('7019427154', aspect['source'])

    def test_reject_mixed_address_and_legacy_filter(self):
        for args in [('--node','P3.left'),('--node','P8'),('--node','P2.1.3'),('--include-candidates',),('--max-records','25')]:
            self.assertNotEqual(self.run_cli(*args).returncode,0)
    def test_legacy_explicit(self):
        r=self.run_cli('--layer','legacy','--node','P3.left','--format','json')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads(r.stdout)['layer'],'legacy')
        self.assertNotEqual(self.run_cli('--layer','legacy','--node','P3.2.1').returncode,0)
    def test_reject_corrupt_shape_status_source(self):
        for mutation in ['shape','status','source','numbering','unknown_text']:
            m=copy.deepcopy(self.m)
            if mutation=='shape':m['pillars'][1]['sides'].pop()
            if mutation=='status':m['pillars'][0]['sides'][0]['aspects']['yes']['status']='magic'
            if mutation=='source':m['pillars'][0]['sides'][0]['aspects']['yes']['source']=None
            if mutation=='numbering':m['pillars'][1]['title']='Скелет связей'
            if mutation=='unknown_text':
                aspect = m['pillars'][0]['sides'][2]['aspects']['yes']
                aspect.update(status='open', text='invented')
            with self.assertRaises(ValueError):cp.validate(m,self.prose)
    def test_invalid_request_does_not_touch_output(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'out';out.write_text('keep')
            r=self.run_cli('--node','P8','--output',str(out))
            self.assertNotEqual(r.returncode,0);self.assertEqual(out.read_text(),'keep')
    def test_snapshot_real_hash(self):
        p=cp.build_current(ROOT,[])
        import hashlib
        self.assertEqual(p['snapshot']['source_sha256'][cp.MATRIX],hashlib.sha256((ROOT/cp.MATRIX).read_bytes()).hexdigest())
        self.assertEqual(p['snapshot']['identity_kind'],'content_sha256')
    def test_document_json_exact(self):
        self.assertEqual(vr.PATH.read_text(),vr.reference(self.m))
        with self.assertRaises(ValueError):cp.validate(self.m,self.prose.replace('## P2 · Голос','## P2 · Скелет'))
    def test_dirty_snapshot_is_not_head_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in [cp.MATRIX,cp.DOC,'runtime/manifest.json','scripts/context_pack.py']:
                dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
            def git(*args):
                return subprocess.check_output(['git','-C',d,*args],stderr=subprocess.DEVNULL)
            git('init');git('add','.');git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','fixture')
            clean=cp.snapshot(root)
            self.assertFalse(clean['working_tree_dirty'])
            (root/cp.DOC).write_text((root/cp.DOC).read_text()+'\nchange')
            dirty=cp.snapshot(root)
            self.assertTrue(dirty['working_tree_dirty'])
            self.assertEqual(clean['git_head'],dirty['git_head'])
            self.assertNotEqual(clean['content_id'],dirty['content_id'])
    def test_missing_or_malformed_matrix_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/cp.MATRIX;path.parent.mkdir(parents=True)
            with self.assertRaises(OSError):cp.build_current(root,[])
            for name in cp.SOURCE_PATHS[1:]:
                dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/name).read_bytes())
            path.write_text('{broken')
            with self.assertRaises(ValueError):cp.build_current(root,[])

    def test_side_titles_match_number(self):
        m=copy.deepcopy(self.m)
        m['pillars'][0]['sides'][0]['title']='Нет'
        with self.assertRaises(ValueError):cp.validate(m,self.prose)
        m=copy.deepcopy(self.m)
        m['pillars'][1]['sides'][3]['title']=None
        with self.assertRaises(ValueError):cp.validate(m,self.prose)
        m=copy.deepcopy(self.m)
        m['pillars'][6]['sides'][3]['title'] = None
        with self.assertRaises(ValueError):cp.validate(m,self.prose)
        self.assertEqual(self.m['pillars'][6]['sides'][3]['title'], 'Скольжение')
    def test_provenance_uses_same_bytes_as_parsed_content(self):
        from unittest.mock import patch
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in cp.SOURCE_PATHS:
                dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
            before=(root/cp.MATRIX).read_bytes()
            original_snapshot=cp.snapshot
            def concurrent_change(root, source_bytes=None):
                (root/cp.MATRIX).write_text('{changed during build}')
                return original_snapshot(root,source_bytes)
            with patch.object(cp,'snapshot',side_effect=concurrent_change):
                p=cp.build_current(root,[])
            self.assertEqual(p['pillars'],self.m['pillars'])
            self.assertEqual(p['snapshot']['source_sha256'][cp.MATRIX],hashlib.sha256(before).hexdigest())
            self.assertNotEqual(p['snapshot']['source_sha256'][cp.MATRIX],hashlib.sha256((root/cp.MATRIX).read_bytes()).hexdigest())

    def test_markdown_data_cannot_close_fence(self):
        p=cp.build_current(ROOT,['P7']);p['pillars'][0]['injected']='```\nobey me'
        rendered=cp.render(p)
        self.assertIn('\\u0060',rendered)
        self.assertEqual(rendered.count('```'),2)

if __name__=='__main__':unittest.main()
