"""Offline checks for safe SSH multiplexing and bounded authentication retries."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('deployment',ROOT/'ops/deploy.py')
deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)

class Transport(unittest.TestCase):
 def test_initial_transient_retry_only(self):
  calls=[]
  def run(args,**kwargs):
   calls.append(args)
   if len(calls)==1:kwargs['stderr'].write('Connection closed by remote host\n')
   return subprocess.CompletedProcess(args,255 if len(calls)==1 else 0)
  with tempfile.TemporaryDirectory() as d,patch.object(deploy.subprocess,'run',side_effect=run),patch.object(deploy.time,'sleep') as sleep:
   deploy.connect_master(['ssh','-p','1234','root@example.com'],Path(d)/'connect.log')
  self.assertEqual(len(calls),2);self.assertEqual(calls[0],calls[1]);self.assertEqual(calls[0][-4:],['-M','-N','-f','root@example.com']);sleep.assert_called_once_with(2)
 def test_host_identity_failure_is_not_retried(self):
  def run(args,**kwargs):
   kwargs['stderr'].write('Host key verification failed.');return subprocess.CompletedProcess(args,255)
  with tempfile.TemporaryDirectory() as d,patch.object(deploy.subprocess,'run',side_effect=run) as mock,patch.object(deploy.time,'sleep') as sleep:
   with self.assertRaises(RuntimeError):deploy.connect_master(['ssh','root@example.com'],Path(d)/'connect.log')
   self.assertEqual(mock.call_count,1);sleep.assert_not_called()
 def test_remote_command_is_never_automatically_replayed(self):
  with patch.object(deploy.subprocess,'run',return_value=subprocess.CompletedProcess([],255)) as run:
   with self.assertRaises(RuntimeError):deploy.run(['ssh','host','python3 deploy_remote.py'])
   run.assert_called_once()
 def test_all_transfer_and_deploy_steps_share_one_private_socket(self):
  calls=[]
  def run(args,**kwargs):calls.append(args);return subprocess.CompletedProcess(args,0)
  config={'port':1234,'host':'example.com','user':'root','identity_file':'/tmp/private key','known_hosts_file':'/tmp/known hosts','project_root':'/workspace/ordering-preview','public_url':'https://example.com/'}
  with tempfile.TemporaryDirectory() as d,patch.object(deploy,'STATE',Path(d)/'state'),patch.object(deploy,'configuration',return_value=config),patch.object(deploy.subprocess,'run',side_effect=run),patch.object(deploy,'public_health'),patch.object(sys,'argv',['deploy']):
   deploy.main()
  self.assertEqual(len([c for c in calls if '-M' in c]),1)
  paths=[a for c in calls for a in c if a.startswith('ControlPath=')]
  self.assertEqual(len(set(paths)),1);self.assertGreaterEqual(len(paths),6)
  self.assertLess(len(paths[0].split('=',1)[1]),100)
  self.assertTrue(all('StrictHostKeyChecking=yes' in c for c in calls))
  self.assertEqual(calls[-1][-3:],['-O','exit','root@example.com'])

if __name__=='__main__':unittest.main()
