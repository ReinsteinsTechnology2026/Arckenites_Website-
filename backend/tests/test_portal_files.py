"""Private files: real isolated SQL/auth endpoints and simulated Microsoft storage."""
import uuid
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from app.config import settings
from app.models import BatchEnrollment
from app.models.portal_file import PortalFile
from app.core.onedrive_store import OneDriveStore, OneDriveError, RemoteObject
from app.core.portal_storage import remote_response, ensure_folder
from tests.portal_storage_fixture import Harness

class FilesTests(Harness):
    def setUp(self):
        super().setUp()
        self.extra = [patch.object(settings, 'portal_onedrive_enabled', True),
                      patch.object(settings, 'onedrive_drive_id', 'DRIVE'),
                      patch('app.core.portal_documents.ensure_folder'),
                      patch('app.core.portal_documents.document_store', side_effect=self.fake_store)]
        for p in self.extra: p.start()
        self.upload_fail = False
    def tearDown(self):
        for p in reversed(self.extra): p.stop()
        super().tearDown()
    def fake_store(self, row=None):
        def upload(path, name):
            if self.upload_fail: raise OneDriveError('fake outage')
            return RemoteObject(item_id='ITEM-' + name, size=path.stat().st_size, quick_xor_hash='HASH')
        return SimpleNamespace(folder='Portal', drive_id='DRIVE', readiness=lambda: (True,None), upload_file=upload)
    def upload(self, user='trainer', scope='materials', target=None, filename='notes.txt', data=b'Class notes'):
        return self.client.post(f'/api/portal-files/scopes/{scope}/{target or self.batch_id}',
            headers=self.headers(user), files={'file': (filename, data)})
    def test_trainer_upload_and_current_membership_on_every_read(self):
        result = self.upload(); self.assertEqual(result.status_code, 201, result.text)
        fid = result.json()['id']; path=f'/api/portal-files/{fid}/download'
        with patch('app.api.routes_portal_files.remote_response', return_value={'ok':True}) as remote:
            self.assertEqual(self.client.get(path,headers=self.headers('student')).status_code,200)
            self.assertEqual(self.client.get(path,headers=self.headers('outsider')).status_code,404)
            with self.sf() as db:
                enrollment=db.scalar(select(BatchEnrollment).where(BatchEnrollment.student_id==self.ids['student']))
                db.delete(enrollment);db.commit()
            self.assertEqual(self.client.get(path,headers=self.headers('student')).status_code,404)
            self.assertEqual(remote.call_count,1)
        with self.sf() as db:
            row=db.get(PortalFile,uuid.UUID(fid));self.assertEqual(row.drive_id,'DRIVE')
            self.assertEqual(row.state,'READY');self.assertTrue(row.remote_item_id)
    def test_notes_require_batch_trainer_or_admin_permission(self):
        self.assertEqual(self.upload(user='student').status_code,404)
        self.assertEqual(self.upload(user='other-trainer').status_code,404)
        self.assertEqual(self.client.get(f'/api/portal-files/scopes/materials/{self.batch_id}',headers=self.headers('outsider')).status_code,404)
    def test_students_may_share_batch_chat_files(self):
        self.assertEqual(self.upload(user='student',scope='batch-chat').status_code,201)
        self.assertEqual(self.upload(user='outsider',scope='batch-chat').status_code,404)
    def test_failed_upload_is_invisible_and_durable_cleanup_retries(self):
        self.upload_fail=True;self.assertEqual(self.upload().status_code,503)
        result=self.client.get(f'/api/portal-files/scopes/materials/{self.batch_id}',headers=self.headers('trainer'))
        self.assertEqual(result.json()['files'],[])
        with self.sf() as db:
            row=db.scalar(select(PortalFile));self.assertEqual(row.state,'FAILED');self.assertTrue(row.remote_name)
        from app.core.portal_storage import process_portal_cleanup
        with patch('app.core.portal_storage.SessionLocal',self.sf),patch('app.core.portal_storage.delete_remote',side_effect=OneDriveError('down')):
            self.assertEqual(process_portal_cleanup(),0)
        with self.sf() as db:self.assertEqual(db.scalar(select(PortalFile)).state,'FAILED')
        with patch('app.core.portal_storage.SessionLocal',self.sf),patch('app.core.portal_storage.delete_remote'):
            self.assertEqual(process_portal_cleanup(),1)
    def test_validation_and_disabled_storage(self):
        self.assertEqual(self.upload(filename='bad.pdf',data=b'not pdf').status_code,400)
        self.assertEqual(self.upload(data=b'').status_code,400)
        self.assertEqual(self.upload(filename='bad.exe').status_code,400)
        with patch('app.core.portal_documents.document_store',return_value=SimpleNamespace(readiness=lambda:(False,'disabled'))):
            self.assertEqual(self.upload().status_code,503)
    def test_direct_chat_files_stay_between_participants(self):
        result=self.upload(user='student',scope='chat',target=self.ids['trainer'])
        self.assertEqual(result.status_code,201,result.text)
        path=f"/api/portal-files/{result.json()['id']}/download"
        with patch('app.api.routes_portal_files.remote_response',return_value={'ok':True}):
            self.assertEqual(self.client.get(path,headers=self.headers('trainer')).status_code,200)
            self.assertEqual(self.client.get(path,headers=self.headers('outsider')).status_code,404)
    def test_delete_is_durable_and_immediately_blocks_download(self):
        fid=self.upload().json()['id'];path=f'/api/portal-files/{fid}'
        self.assertEqual(self.client.delete(path,headers=self.headers('student')).status_code,403)
        self.assertEqual(self.client.delete(path,headers=self.headers('trainer')).status_code,204)
        self.assertEqual(self.client.get(path+'/download',headers=self.headers('student')).status_code,404)
        with self.sf() as db:self.assertEqual(db.get(PortalFile,uuid.UUID(fid)).state,'DELETING')
    def test_support_upload_preserves_existing_ticket_authorization(self):
        create=self.client.post('/api/students/me/tickets',headers=self.headers('student'),
            json={'subject':'Help','category':'technical_issue','description':'Please help'})
        self.assertEqual(create.status_code,201,create.text)
        ticket=create.json(); mid=ticket['messages'][0]['id']; tid=ticket['id']
        upload=self.client.post(f'/api/students/me/tickets/{tid}/messages/{mid}/attachments',
            headers=self.headers('student'),files={'file':('help.txt',b'Help notes')})
        self.assertEqual(upload.status_code,201,upload.text)
        aid=upload.json()['id'];path=f'/api/students/me/tickets/{tid}/attachments/{aid}'
        with patch('app.core.portal_storage.remote_response',return_value={'ok':True}) as remote:
            self.assertEqual(self.client.get(path,headers=self.headers('student')).status_code,200)
            self.assertEqual(self.client.get(path,headers=self.headers('outsider')).status_code,404)
            self.assertEqual(remote.call_count,1)

    def test_pagination_does_not_drop_older_files(self):
        from datetime import datetime, timezone
        with self.sf() as db:
            for n in range(101):
                db.add(PortalFile(scope='materials',target_id=self.batch_id,uploader_id=self.ids['trainer'],
                    drive_id='DRIVE',folder='Portal',remote_name=f'{n}.txt',remote_item_id=f'I{n}',
                    file_name=f'{n}.txt',file_type='text/plain',size_bytes=1,state='READY'))
            db.commit()
        path=f'/api/portal-files/scopes/materials/{self.batch_id}'
        first=self.client.get(path,headers=self.headers('student')).json()
        self.assertEqual(len(first['files']),100);self.assertEqual(first['next_offset'],100)
        last=self.client.get(path+'?offset=100',headers=self.headers('student')).json()
        self.assertEqual(len(last['files']),1);self.assertIsNone(last['next_offset'])
        self.assertNotIn(last['files'][0]['id'],{f['id'] for f in first['files']})

    def test_ready_objects_without_parent_are_cleaned_only_after_grace_period(self):
        from datetime import datetime, timezone, timedelta
        from app.core.portal_storage import process_portal_cleanup
        fid=self.upload().json()['id']
        with self.sf() as db:
            row=db.get(PortalFile,uuid.UUID(fid));row.target_id=99999
            row.created_at=datetime.now(timezone.utc)-timedelta(days=2);db.commit()
        with patch('app.core.portal_storage.SessionLocal',self.sf),patch('app.core.portal_storage.delete_remote') as remove:
            self.assertEqual(process_portal_cleanup(),1);self.assertEqual(remove.call_count,1)

    def test_recording_upload_and_scoped_playback_recheck_membership(self):
        from starlette.responses import Response
        data=b'\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42'
        result=self.upload(scope='videos',filename='lesson.mp4',data=data)
        self.assertEqual(result.status_code,201,result.text)
        fid=result.json()['id'];path=f'/api/portal-files/{fid}/playback-token'
        self.assertEqual(self.client.post(path,headers=self.headers('outsider')).status_code,404)
        token=self.client.post(path,headers=self.headers('student')).json()['stream_url'].split('t=')[1]
        stream=f'/api/portal-files/{fid}/stream'
        with patch('app.api.routes_portal_files.remote_response',return_value=Response(content=b'abc',status_code=206)) as remote:
            response=self.client.get(stream,params={'t':token},headers={'Range':'bytes=0-2'})
            self.assertEqual(response.status_code,206)
            self.assertEqual(remote.call_args.kwargs['byte_range'],(0,2))
            with self.sf() as db:
                enrollment=db.scalar(select(BatchEnrollment).where(BatchEnrollment.student_id==self.ids['student']))
                db.delete(enrollment);db.commit()
            self.assertEqual(self.client.get(stream,params={'t':token}).status_code,404)
            self.assertEqual(remote.call_count,1)
        self.assertEqual(self.client.get(stream,params={'t':'not-a-valid-token'}).status_code,401)

    def test_recordings_reject_wrong_container_oversize_and_student_uploads(self):
        self.assertEqual(self.upload(scope='videos',filename='lesson.mp4',data=b'not mp4').status_code,400)
        self.assertEqual(self.upload(user='student',scope='videos',filename='lesson.mp4',data=b'x'*30).status_code,404)
        with patch('app.core.portal_documents.MAX_VIDEO_BYTES',16):
            self.assertEqual(self.upload(scope='videos',filename='lesson.mp4',data=b'x'*30).status_code,413)

    def test_support_objects_cannot_bypass_ticket_authorization(self):
        fid=self.upload().json()['id']
        with self.sf() as db:
            row=db.get(PortalFile,uuid.UUID(fid));row.scope='support';db.commit()
        self.assertEqual(self.client.get(f'/api/portal-files/{fid}/download',headers=self.headers('trainer')).status_code,404)

class GraphContentTests(unittest.TestCase):
    def store(self, handler):
        return OneDriveStore(drive_id='DRIVE',folder='Portal',token_provider=lambda:'PRIVATE-TOKEN',
                            http=httpx.Client(transport=httpx.MockTransport(handler)))
    def test_redirect_strips_bearer_and_preserves_range(self):
        calls=[]
        def handler(req):
            calls.append(req)
            if req.url.host=='graph.microsoft.com':
                return httpx.Response(302,headers={'location':'https://storage.example.test/content'})
            return httpx.Response(206,content=b'abc',headers={'Content-Range':'bytes 0-2/8'})
        store=self.store(handler)
        response=remote_response(store,'ITEM',size=8,media_type='video/mp4',byte_range=(0,2),head=True)
        self.assertEqual(response.status_code,206)
        self.assertEqual(calls[0].headers['Authorization'],'Bearer PRIVATE-TOKEN')
        self.assertNotIn('Authorization',calls[1].headers)
        self.assertEqual(calls[1].headers['Range'],'bytes=0-2');store._http.close()
    def test_wrong_size_or_ignored_range_is_not_served(self):
        for kwargs in ({'size':9},{'size':3,'byte_range':(0,1)}):
            store=self.store(lambda req:httpx.Response(200,content=b'abc'))
            with self.assertRaises(HTTPException) as exc:remote_response(store,'ITEM',media_type='video/mp4',**kwargs)
            self.assertEqual(exc.exception.status_code,503);store._http.close()
    def test_folder_creation_checks_every_component(self):
        seen=[]
        def handler(req):
            seen.append((req.method,str(req.url)))
            if req.method=='GET':return httpx.Response(404,json={})
            return httpx.Response(201,json={'id':str(len(seen)),'folder':{}})
        store=self.store(handler);store.folder='Portal/Materials';ensure_folder(store)
        self.assertEqual([m for m,u in seen],['GET','POST','GET','POST']);store._http.close()
