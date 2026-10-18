import asyncio
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

from app.core import uploads
from app.core.uploads import _content_matches_extension, read_upload_limited


class UploadLimitTests(unittest.TestCase):
    def test_reader_stops_after_limit_plus_one_byte(self):
        file = UploadFile(filename="sample.txt", file=io.BytesIO(b"abcdef"))
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(read_upload_limited(file, 4))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(file.file.tell(), 5)

    def test_reader_accepts_exact_limit(self):
        file = UploadFile(filename="sample.txt", file=io.BytesIO(b"abcd"))
        self.assertEqual(asyncio.run(read_upload_limited(file, 4)), b"abcd")

    def test_content_signatures_reject_disguised_files(self):
        for extension in (".jpg", ".png", ".gif", ".webp", ".pdf", ".doc", ".docx"):
            with self.subTest(extension=extension):
                self.assertFalse(_content_matches_extension(extension, b"MZ executable"))
        self.assertFalse(_content_matches_extension(".txt", b"hello\x00world"))
        self.assertFalse(_content_matches_extension(".docx", b"PK\x03\x04invalid"))

    def test_content_signatures_accept_supported_headers(self):
        cases = {
            ".jpg": b"\xff\xd8\xff\xe0",
            ".png": b"\x89PNG\r\n\x1a\nrest",
            ".gif": b"GIF89arest",
            ".webp": b"RIFF\x04\x00\x00\x00WEBPrest",
            ".pdf": b"%PDF-1.7\n",
            ".doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest",
            ".txt": "Hello, world\n".encode(),
        }
        for extension, contents in cases.items():
            with self.subTest(extension=extension):
                self.assertTrue(_content_matches_extension(extension, contents))
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
        self.assertTrue(_content_matches_extension(".docx", archive_bytes.getvalue()))

    def test_spoofed_file_and_photo_are_rejected_before_disk_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(uploads, "UPLOAD_ROOT", root), patch.object(uploads, "PROFILE_PHOTO_ROOT", root):
                with self.assertRaises(HTTPException):
                    uploads.save_upload(UploadFile(filename="fake.pdf", file=io.BytesIO()), b"MZ executable")
                with self.assertRaises(HTTPException):
                    uploads.save_profile_photo(UploadFile(filename="fake.png", file=io.BytesIO()), b"MZ executable")
            self.assertEqual(list(root.iterdir()), [])
