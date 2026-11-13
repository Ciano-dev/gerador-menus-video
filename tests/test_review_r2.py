"""Prévia técnica MP4 de autoria AVI: isolamento e FFmpeg real curto."""
import copy
import json
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

from gui_autoria import MediaInfo, Project, Scene
import gui_servicos as services
import media_runtime as runtime
import menu_video as motor


class PreviewIsolationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='review-r2-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        source, music = self.base / 'synthetic.mp4', self.base / 'synthetic.wav'
        source.write_bytes(b'synthetic source placeholder for unit isolation')
        music.write_bytes(b'synthetic music placeholder for unit isolation')
        self.project = Project(title='Prévia AVI sintética', duration=.4, export_format='AVI',
            title_alignment='centralizado', scenes=[Scene(source, .4, 'Cena', duration=.2)],
            music=MediaInfo(music, .4))
        self.created = []
        mkdtemp = tempfile.mkdtemp
        def create(suffix=None, prefix=None, dir=None, **kwargs):
            if prefix != 'menu-previa-':
                return mkdtemp(suffix, prefix, dir, **kwargs)
            path = Path(mkdtemp(suffix, prefix, self.base, **kwargs))
            self.created.append(path)
            return str(path)
        factory = patch.object(services.tempfile, 'mkdtemp', side_effect=create)
        factory.start()
        self.addCleanup(factory.stop)

    def remember(self):
        project = self.project
        self.semantic = copy.deepcopy(project)
        self.bytes_before = pickle.dumps(project)
        self.json_before = json.dumps(services.project_data(project, self.base), sort_keys=True).encode()
        self.references = (project.__dict__, project.scenes, project.colors,
                           project.scenes[0], project.scenes[0].__dict__, project.music,
                           project.music.__dict__)
        self.sources = {path: motor.sha256(path) for path in (project.scenes[0].source, project.music.source)}

    def assert_original(self):
        project = self.project
        self.assertEqual(project, self.semantic)
        self.assertEqual(pickle.dumps(project), self.bytes_before)
        self.assertEqual(json.dumps(services.project_data(project, self.base), sort_keys=True).encode(), self.json_before)
        references = (project.__dict__, project.scenes, project.colors,
                      project.scenes[0], project.scenes[0].__dict__, project.music,
                      project.music.__dict__)
        for current, original in zip(references, self.references):
            self.assertIs(current, original)
        self.assertEqual({path: motor.sha256(path) for path in self.sources}, self.sources)

    def assert_copy(self, candidate):
        self.assertIsNot(candidate, self.project)
        self.assertEqual(candidate.export_format, 'MP4')
        expected = copy.deepcopy(self.project)
        expected.export_format = 'MP4'
        self.assertEqual(candidate, expected)
        for value, original in [(candidate.scenes, self.project.scenes),
                                (candidate.colors, self.project.colors),
                                (candidate.scenes[0], self.project.scenes[0]),
                                (candidate.music, self.project.music)]:
            self.assertIsNot(value, original)
        self.assert_original()

    def test_success_passes_isolated_mp4_project_and_preserves_original_during_render(self):
        self.remember()
        token = runtime.CancellationToken()
        events = []
        def render(candidate, directory, output, *, profile, progress, cancel):
            self.assert_copy(candidate)
            self.assertEqual((output.suffix, profile), ('.mp4', 'prova'))
            self.assertIs(cancel, token)
            candidate.colors['texto'] = '#123456'
            candidate.scenes[0].name = 'Alterado só na cópia'
            progress('fixture')
            self.assert_original()
            output.write_bytes(b'synthetic preview for isolation test')
            return output
        with patch.object(services, 'render_project', side_effect=render):
            result = services.preview_project(self.project, progress=events.append, cancel=token)
        self.assertEqual(events, ['fixture'])
        self.assertEqual(list(result.parent.iterdir()), [result])
        self.assert_original()

    def test_failure_and_cancel_preserve_original_and_remove_partial_preview(self):
        for outcome in ('failure', 'cancel'):
            with self.subTest(outcome=outcome):
                self.remember()
                token = runtime.CancellationToken()
                def render(candidate, directory, output, **kwargs):
                    self.assert_copy(candidate)
                    candidate.scenes.clear()
                    candidate.colors.clear()
                    output.write_bytes(b'partial synthetic output')
                    (directory / 'partial.tmp').write_bytes(b'partial synthetic work')
                    self.assert_original()
                    if outcome == 'cancel':
                        self.assertIs(kwargs['cancel'], token)
                        token.cancel()
                        token.check()
                    raise motor.ErroProjeto('falha sintética')
                error = runtime.Cancelled if outcome == 'cancel' else motor.ErroProjeto
                with patch.object(services, 'render_project', side_effect=render):
                    with self.assertRaises(error):
                        services.preview_project(self.project, cancel=token)
                self.assertTrue(self.created)
                self.assertTrue(all(not path.exists() for path in self.created))
                self.assert_original()

    def test_real_avi_project_produces_decodable_mp4_and_preserves_original(self):
        ffmpeg = motor.resolve_tool('ffmpeg')
        ffprobe = motor.resolve_tool('ffprobe')
        source, music = self.base / 'real.mp4', self.base / 'real.wav'
        motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
            'testsrc2=s=64x64:r=30:d=0.4', '-c:v', 'libx264', str(source)])
        motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
            'sine=duration=0.4:sample_rate=48000', str(music)])
        self.project.scenes[0].source = source
        self.project.music = MediaInfo(music, .4)
        self.remember()
        try:
            result = services.preview_project(self.project, progress=lambda message: self.assert_original())
        except motor.ErroProjeto as exc:
            self.fail(f'A prévia técnica deve aceitar autoria AVI e gerar MP4: {exc}')
        details = motor.probe_media(ffprobe, result)
        self.assertEqual(result.suffix, '.mp4')
        self.assertIn('mp4', details['format']['format_name'].split(','))
        video = next(stream for stream in details['streams'] if stream['codec_type'] == 'video')
        self.assertEqual((video['codec_name'], video['width'], video['height']), ('h264', 640, 360))
        self.assertAlmostEqual(float(details['format']['duration']), .4, delta=.05)
        decoded = motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-i', str(result),
            '-map', '0:v:0', '-pix_fmt', 'rgb24', '-threads', '1', '-f', 'rawvideo', '-']).stdout
        self.assertEqual(len(decoded), 12 * 640 * 360 * 3)
        motor.check_decode(ffmpeg, result, duration=.4)
        self.assertEqual(list(result.parent.iterdir()), [result])
        self.assert_original()
        print('R2 REAL: AVI project -> MP4 h264 640x360; 12 frames decoded; '
              'original pickle/JSON/semantics/identities and source SHA256 intact.', flush=True)

    def test_real_render_precancelled_avi_preserves_model_and_cleans_directory(self):
        self.remember()
        token = runtime.CancellationToken()
        token.cancel()
        with patch.object(services.subprocess, 'Popen') as spawn:
            with self.assertRaises(runtime.Cancelled):
                services.preview_project(self.project, cancel=token)
        spawn.assert_not_called()
        self.assertTrue(self.created)
        self.assertTrue(all(not path.exists() for path in self.created))
        self.assert_original()


if __name__ == '__main__':
    unittest.main()
