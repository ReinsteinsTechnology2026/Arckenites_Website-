"""Validation edge cases for portal files (verification branch)."""
from unittest.mock import patch
from sqlalchemy import select
from app.core.uploads import MAX_FILE_SIZE_BYTES
from app.models.portal_file import PortalFile
from tests import test_portal_files as _m


class ValidationEdges(_m.FilesTests):

    def test_long_filename_keeps_a_working_extension(self):
        name = ('a' * 300) + '.pdf'
        r = self.upload(filename=name, data=b'%PDF-1.4 test')
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(len(r.json()['file_name']), 255)
        self.assertTrue(r.json()['file_name'].endswith('.pdf'))

    def test_long_name_without_extension_is_still_rejected(self):
        self.assertEqual(self.upload(filename='b' * 300, data=b'hello').status_code, 400)

    def test_document_size_boundary(self):
        ok = b'%PDF-' + b'x' * (MAX_FILE_SIZE_BYTES - 5)
        self.assertEqual(self.upload(filename='edge.pdf', data=ok).status_code, 201)
        self.assertEqual(self.upload(filename='edge2.pdf', data=ok + b'x').status_code, 413)

    def test_recording_size_boundary(self):
        mp4 = b'\x00\x00\x00\x18ftypisom' + b'\x00' * 40
        with patch('app.core.portal_documents.MAX_VIDEO_BYTES', len(mp4)):
            self.assertEqual(self.upload(scope='videos', filename='edge.mp4', data=mp4).status_code, 201)
            self.assertEqual(self.upload(scope='videos', filename='edge2.mp4', data=mp4 + b'x').status_code, 413)

    def test_rejections_leave_no_rows(self):
        cases = [('empty.pdf', b''), ('fake.pdf', b'MZ\x90\x00 pretend binary'), ('fake.docx', b'plain text'),
                 ('run.exe', b'MZ'), ('noext', b'hello'), ('page.html', b'<html>'), ('double.pdf.exe', b'%PDF-1')]
        for name, data in cases:
            self.assertEqual(self.upload(filename=name, data=data).status_code, 400, name)
        with self.sf() as db:
            self.assertEqual(db.scalar(select(PortalFile.id)), None)

    def test_path_traversal_names_are_reduced_to_basename(self):
        r = self.upload(filename='..\..\evil/../notes.txt', data=b'hi')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['file_name'], 'notes.txt')


# Inherit the fixtures only; do not re-run the original tests.
for _n in [n for n in dir(_m.FilesTests) if n.startswith('test_') and n not in ValidationEdges.__dict__]:
    setattr(ValidationEdges, _n, None)
