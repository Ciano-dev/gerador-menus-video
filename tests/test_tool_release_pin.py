"""Pinned retained vendor snapshot, not a mutable latest or expired daily link."""
from pathlib import Path
import json
import unittest
import client_download

ROOT = Path(__file__).resolve().parents[1]
URL = ('https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/'
       'autobuild-2026-10-31-12-59/'
       'ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-9.0.zip')


class RetainedToolReleaseTests(unittest.TestCase):
    def test_runtime_and_manifest_use_fully_verified_monthly_archive(self):
        data = json.loads((ROOT/'windows_build/client-download-manifest.json').read_text())
        self.assertEqual(data['binary']['url'], URL)
        self.assertEqual(data['binary']['sha256'],
                         '693f3e224bc184b40524a62ca47526cfebaf2504414021b92fe75f677ad868b9')
        self.assertEqual(data['binary']['size_bytes'], 195110184)
        prefix = 'ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-9.0/bin/'
        self.assertEqual(data['binary']['ffmpeg_member'], prefix+'ffmpeg.exe')
        self.assertEqual(data['binary']['ffprobe_member'], prefix+'ffprobe.exe')
        self.assertEqual(client_download.PINNED_URL, URL)
        self.assertEqual(client_download.validate_manifest(data), data)

    def test_retained_pin_does_not_authorize_mutable_or_altered_urls(self):
        try:
            accepted = client_download.initial_url(URL)
        except ValueError as error:
            self.fail('Verified monthly snapshot rejected: '+str(error))
        self.assertEqual(accepted, URL)
        for changed in (URL.replace('2026-10-31', '2026-10-30'),
                        URL.replace('autobuild-2026-10-31-12-59', 'latest'),
                        URL+'?download=1', URL.replace('https:', 'http:')):
            with self.subTest(url=changed), self.assertRaises(ValueError):
                client_download.initial_url(changed)
