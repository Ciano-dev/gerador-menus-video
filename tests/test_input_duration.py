"""Regressões de entrada com arquivos sintéticos reais, sem remux de produto."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import copy
from fractions import Fraction
from unittest.mock import patch
import threading
import os
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui_servicos as services
import menu_video as motor


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg necessário')
class InputDurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='input-duration-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def make(self, name, arguments):
        path = self.base / name
        motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + arguments + [str(path)])
        return path

    def test_real_mkv_track_tag_not_longer_audio(self):
        source = self.make('tag.mkv', [
            '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=30:duration=1',
            '-f', 'lavfi', '-i', 'sine=sample_rate=48000:duration=1.5',
            '-map', '1:a', '-map', '0:v', '-c:v', 'libx264', '-bf', '2',
            '-threads', '1', '-c:a', 'pcm_s16le'])
        details = motor.probe_media(motor.resolve_tool('ffprobe'), source)
        video = next(s for s in details['streams'] if s['codec_type'] == 'video')
        self.assertNotIn('duration', video)
        self.assertNotIn('duration_ts', video)
        self.assertEqual(video['tags']['DURATION'], '00:00:01.000000000')
        self.assertGreater(float(details['format']['duration']), 1)
        self.assertEqual(services.inspect_media(source).duration, 1)

    def test_real_live_matroska_bframes_requires_timestamp_measurement(self):
        source = self.make('live.mkv', [
            '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=30:duration=1',
            '-f', 'lavfi', '-i', 'sine=sample_rate=48000:duration=1.5',
            '-map', '1:a', '-map', '0:v', '-c:v', 'libx264', '-bf', '2',
            '-threads', '1', '-c:a', 'pcm_s16le', '-live', '1'])
        details = motor.probe_media(motor.resolve_tool('ffprobe'), source)
        video = next(s for s in details['streams'] if s['codec_type'] == 'video')
        self.assertNotIn('duration', video)
        self.assertNotIn('DURATION', video.get('tags', {}))
        self.assertNotIn('duration', details['format'])
        info = motor.input_duration_info(motor.resolve_tool('ffprobe'), source, video, details)
        self.assertEqual(info['method'], 'timestamps.frames')
        self.assertAlmostEqual(info['seconds'], 1, delta=.001)
        self.assertEqual(info['stream_index'], 1)
        self.assertAlmostEqual(services.inspect_media(source).duration, 1, delta=.001)

    def test_real_vfr_offset_and_explicit_tail(self):
        source = self.make('offset-vfr.mkv', [
            '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10:duration=1',
            '-vf', "select='eq(n,0)+eq(n,1)+eq(n,4)+eq(n,9)',setpts=PTS+2/TB",
            '-fps_mode', 'vfr', '-c:v', 'ffv1', '-live', '1'])
        details = motor.probe_media(motor.resolve_tool('ffprobe'), source)
        video = details['streams'][0]
        self.assertEqual(float(video['start_time']), 2)
        info = motor.input_duration_info(motor.resolve_tool('ffprobe'), source, video, details)
        self.assertEqual(info['method'], 'timestamps.frames')
        self.assertEqual(info['frames'], 4)
        self.assertEqual(Fraction(info['first_timestamp']), 2)
        self.assertEqual(Fraction(info['end_timestamp']), 3)
        self.assertEqual(info['seconds'], 1)

    def test_real_webm_if_encoder_available(self):
        encoders = motor.run_media([motor.resolve_tool('ffmpeg'), '-encoders']).stdout
        if b'libvpx-vp9' not in encoders:
            self.skipTest('Encoder WebM VP9 indisponível')
        source = self.make('tag.webm', ['-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=10:duration=1', '-c:v', 'libvpx-vp9',
            '-deadline', 'realtime', '-cpu-used', '8', '-threads', '1'])
        self.assertEqual(services.inspect_media(source).duration, 1)

    def test_tags_case_variants_strict_parse_and_contradictions_measure_real_file(self):
        source = self.make('tag-validation.mkv', ['-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=10:duration=1', '-c:v', 'ffv1'])
        details = motor.probe_media(motor.resolve_tool('ffprobe'), source)
        original = details['streams'][0]
        for key in ('DURATION', 'duration', 'Duration', 'DuRaTiOn'):
            video = copy.deepcopy(original)
            video['tags'] = {key: '00:00:01.000000000'}
            with self.subTest(key=key), patch.object(motor.media_runtime, 'stream_lines') as scan:
                info = motor.input_duration_info(motor.resolve_tool('ffprobe'), source, video, details)
                self.assertEqual(info['seconds'], 1)
                self.assertEqual(info['method'], 'tags.' + key)
                scan.assert_not_called()
        # Só os metadados são doubles; toda decisão de fallback mede o MKV real.
        for value in (True, False, None, 1, 'NaN', 'inf', '-00:00:01.0',
                      '00:60:00.0', '00:00:60.0', '0:00:01.0', '00:00:01',
                      '00:00:01.0x', ' 00:00:01.0', '00:00:00.0', '00:00:09.0'):
            video = copy.deepcopy(original)
            video['tags'] = {'DURATION': value}
            with self.subTest(value=value):
                info = motor.input_duration_info(motor.resolve_tool('ffprobe'), source, video, details)
                self.assertEqual(info['method'], 'timestamps.frames')
                self.assertEqual(info['seconds'], 1)
        for extra in ({'duration': '9'}, {'duration_ts': True}, {'duration': 'nan'},
                      {'start_time': '2'}, {'start_time': '-2'}, {'start_time': 'inf'}):
            video = dict(original, **extra)
            with self.subTest(extra=extra):
                info = motor.input_duration_info(motor.resolve_tool('ffprobe'), source, video, details)
                self.assertEqual(info['method'], 'timestamps.frames')
                self.assertEqual(info['seconds'], 1)

    def test_mp4_wav_fast_path_and_rational_one_frame_preserved(self):
        for name, args in (
            ('fast.mp4', ['-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=30:duration=1', '-c:v', 'libx264']),
            ('fast.wav', ['-f', 'lavfi', '-i', 'sine=sample_rate=48000', '-t', '0.0333333333333', '-c:a', 'pcm_s16le'])):
            source = self.make(name, args)
            with self.subTest(name=name), patch.object(motor.media_runtime, 'stream_lines') as scan:
                info = services.inspect_media(source, audio=name.endswith('.wav'))
                self.assertEqual(info.duration, 1/30 if name.endswith('.wav') else 1)
                scan.assert_not_called()

    def test_explicit_mp4_ticks_with_offset_remain_fast_and_small_conflicts_measure(self):
        source = self.make('offset-fast.mp4', ['-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=30:duration=1', '-c:v', 'libx264', '-output_ts_offset', '2'])
        with patch.object(motor.media_runtime, 'stream_lines') as scan:
            self.assertEqual(services.inspect_media(source).duration, 1)
            scan.assert_not_called()
        source = self.make('conflict.mkv', ['-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=10:duration=1', '-c:v', 'ffv1'])
        details = motor.probe_media(motor.resolve_tool('ffprobe'), source)
        video = dict(details['streams'][0], duration='1', tags={'DURATION': '00:00:01.001000000'})
        info = motor.input_duration_info(motor.resolve_tool('ffprobe'), source, video, details)
        self.assertEqual(info['method'], 'timestamps.frames')
        self.assertEqual(info['seconds'], 1)

    def test_real_measured_one_frame_keeps_rational_precision(self):
        source = self.make('measured-one-frame.mp4', ['-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=30:duration=1', '-frames:v', '1', '-c:v', 'libx264'])
        details = motor.probe_media(motor.resolve_tool('ffprobe'), source)
        info = motor.measure_input_duration(motor.resolve_tool('ffprobe'), source, details['streams'][0])
        self.assertEqual(info['seconds'], 1/30)
        self.assertEqual(services.inspect_media(source).duration, 1/30)

    def test_measurement_rejects_partial_decode_even_if_ffprobe_exits_zero(self):
        source = self.make('partial.mp4', ['-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=30:duration=1', '-c:v', 'libx264', '-threads', '1'])
        content = bytearray(source.read_bytes())
        position = content.index(b'mdat')
        size = int.from_bytes(content[position-4:position], 'big')
        end = position - 4 + size
        start = position + 4 + (size-8) // 2
        content[start:end] = bytes(end-start)
        source.write_bytes(content)
        details = motor.probe_media(motor.resolve_tool('ffprobe'), source)
        with self.assertRaisesRegex(motor.ErroProjeto, 'indeterminada'):
            motor.measure_input_duration(motor.resolve_tool('ffprobe'), source, details['streams'][0])

    def test_tag_without_start_is_ambiguous_and_audio_priming_is_decoded(self):
        source = self.make('unknown-start.mkv', ['-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=10:duration=1', '-c:v', 'ffv1'])
        details = motor.probe_media(motor.resolve_tool('ffprobe'), source)
        video = dict(details['streams'][0])
        video.pop('start_time', None)
        info = motor.input_duration_info(motor.resolve_tool('ffprobe'), source, video, details)
        self.assertEqual(info['method'], 'timestamps.frames')
        self.assertEqual(info['seconds'], 1)
        source = self.make('priming-live.mkv', ['-f', 'lavfi', '-i',
            'sine=sample_rate=48000:duration=1', '-c:a', 'aac', '-live', '1'])
        details = motor.probe_media(motor.resolve_tool('ffprobe'), source)
        audio = details['streams'][0]
        info = motor.input_duration_info(motor.resolve_tool('ffprobe'), source, audio, details)
        self.assertEqual(info['method'], 'timestamps.frames')
        # AAC no Matroska não conserva skip_samples do MP4: inclui as amostras
        # efetivamente decodificadas (priming/padding), sem inventar 1s exato.
        raw = motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + [
            '-i', str(source), '-map', '0:a:0', '-ac', '1', '-ar', '48000', '-f', 's16le', '-']).stdout
        self.assertAlmostEqual(info['seconds'], len(raw) / 2 / 48000, delta=.001)

    def test_real_attached_picture_is_excluded_from_selected_timestamp_track(self):
        from test_streams import create_media
        source, _ = create_media(self.base / 'cover')
        details = motor.probe_media(motor.resolve_tool('ffprobe'), source)
        self.assertTrue(details['streams'][0]['disposition']['attached_pic'])
        video = details['streams'][1]
        video.pop('duration', None)
        video.pop('duration_ts', None)
        # Double só remove metadados para exigir fallback; índice e arquivos,
        # inclusive a capa anterior à filmagem, continuam reais.
        with patch.object(motor, 'probe_media', return_value=details):
            self.assertEqual(services.inspect_media(source).duration, 1)
        info = motor.measure_input_duration(motor.resolve_tool('ffprobe'), source, video)
        self.assertEqual(info['stream_index'], 1)
        self.assertEqual(info['frames'], 5)
        self.assertEqual(info['seconds'], 1)

    def test_missing_timestamp_or_tail_in_a_frame_is_not_silently_skipped(self):
        # Controle identificado de resposta malformada: ffprobe normal não
        # permite solicitar um frame específico sem PTS. Medição real dos
        # formatos válidos e decode parcial está coberta separadamente.
        for last in ('duration=1|duration_time=0.1', 'pts=1|pts_time=0.1'):
            def malformed(arguments, consume, **kwargs):
                consume('pts=0|pts_time=0.0|duration=1|duration_time=0.1')
                consume(last)
                return b''
            with self.subTest(last=last), patch.object(motor.media_runtime, 'stream_lines', side_effect=malformed), \
                    self.assertRaisesRegex(motor.ErroProjeto, 'indeterminada'):
                motor.measure_input_duration('ffprobe', self.base / 'controlled.mkv',
                    {'index': 0, 'codec_type': 'video', 'time_base': '1/10'})

    def test_invalid_media_and_invalid_selected_index_fail_honestly(self):
        source = self.base / 'invalid.mkv'
        source.write_bytes(b'not matroska')
        with self.assertRaises(motor.ErroProjeto):
            services.inspect_media(source)
        for index in (True, -1, '0', '0;touch /tmp/no', 1.5, None):
            with self.subTest(index=index), self.assertRaises(motor.ErroProjeto), \
                    patch.object(subprocess, 'Popen') as spawn:
                motor.measure_input_duration('ffprobe', source, {'index': index, 'codec_type': 'video'})
            spawn.assert_not_called()

    def test_real_fallback_cancel_timeout_retry_reaps_only_own_children(self):
        runtime = motor.media_runtime
        source = self.make('retry-live.mkv', ['-f', 'lavfi', '-i',
            'testsrc2=size=320x180:rate=30:duration=2', '-c:v', 'libx264',
            '-threads', '1', '-live', '1'])
        details = motor.probe_media(motor.resolve_tool('ffprobe'), source)
        video = details['streams'][0]
        before = motor.sha256(source)
        children, timers = [], []
        original_popen = subprocess.Popen
        other = original_popen([sys.executable, '-c', 'import time; time.sleep(10)'],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            descriptors = len(os.listdir('/proc/self/fd')) if Path('/proc/self/fd').is_dir() else None
            for mode in ('cancel', 'timeout', 'cancel', 'timeout'):
                token = runtime.CancellationToken()
                def spawn(*args, **kwargs):
                    child = original_popen(*args, **kwargs)
                    children.append(child)
                    if mode == 'cancel':
                        timer = threading.Timer(.001, token.cancel)
                        timers.append(timer)
                        timer.start()
                    return child
                # Double apenas para observar PIDs e controlar o instante do
                # cancelamento. O processo e a leitura ffprobe são reais.
                with self.subTest(mode=mode), patch.object(subprocess, 'Popen', side_effect=spawn), \
                        runtime.cancellation_scope(token):
                    with self.assertRaises(runtime.Cancelled if mode == 'cancel' else motor.ErroProjeto):
                        motor.measure_input_duration(motor.resolve_tool('ffprobe'), source, video,
                                                     timeout=.001 if mode == 'timeout' else 3)
                self.assertIsNone(other.poll(), 'Cancelamento encerrou processo de outra operação')
                self.assertIsNotNone(children[-1].poll())
                self.assertTrue(children[-1].stdout.closed and children[-1].stderr.closed)
            for timer in timers:
                timer.join()
            self.assertFalse(any(t.name.startswith('menu-video-probe-') for t in threading.enumerate()))
            if descriptors is not None:
                self.assertLessEqual(len(os.listdir('/proc/self/fd')), descriptors)
            measured = motor.input_duration_info(motor.resolve_tool('ffprobe'), source, video, details)
            self.assertEqual(measured['method'], 'timestamps.frames')
            self.assertAlmostEqual(measured['seconds'], 2, delta=.001)
            self.assertEqual(motor.sha256(source), before)
            print('Fallback real cancel/timeout/retry: children=', [p.pid for p in children],
                  'reaped=', [p.returncode for p in children], flush=True)
        finally:
            for timer in timers:
                timer.join()
            other.terminate()
            other.communicate(timeout=2)
            other.stdout.close()
            other.stderr.close()

    def test_streaming_reader_bounds_and_failure_cleanup_with_real_control_child(self):
        runtime = motor.media_runtime
        children = []
        original = subprocess.Popen
        def spawn(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        # Filhos Python controlados só para limites de linha/erro; os testes
        # acima exercitam a medição efetiva com ffprobe real.
        with patch.object(subprocess, 'Popen', side_effect=spawn):
            with self.assertRaises(ValueError):
                runtime.stream_lines([sys.executable, '-c',
                    "import sys,time; sys.stderr.write('e'*100000); sys.stderr.flush(); sys.stdout.write('x'*70000+'\\n'); sys.stdout.flush(); time.sleep(3)"],
                    lambda line: None, timeout=3)
            with self.assertRaises(RuntimeError):
                runtime.stream_lines([sys.executable, '-c',
                    "import sys,time; print('frame=1',flush=True); time.sleep(3)"],
                    lambda line: (_ for _ in ()).throw(RuntimeError('consume failed')), timeout=4)
        self.assertTrue(all(c.poll() is not None and c.stdout.closed and c.stderr.closed for c in children))


class StreamLinesConstructorTests(unittest.TestCase):
    def test_constructor_memory_errors_leave_no_children_pipes_or_threads(self):
        runtime = motor.media_runtime
        real_popen = subprocess.Popen
        real_queue, real_event, real_thread = runtime.Queue, runtime.Event, runtime.Thread
        release = real_event()
        controls = [real_thread(target=release.wait, name=f'constructor-control-{i}')
                    for i in range(2)]
        other = None

        def cleanup(children, readers):
            for child in children:
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
            for reader in readers:
                if reader.ident is not None:
                    reader.join(timeout=2)
            for child in children:
                child.stdout.close()
                child.stderr.close()

        try:
            for control in controls:
                control.start()
            other = real_popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for fault, occurrence in (('Queue', 1), ('Event', 1), ('Thread', 1), ('Thread', 2)):
                with self.subTest(constructor=fault, occurrence=occurrence):
                    children, readers = [], []
                    calls = {'Queue': 0, 'Event': 0, 'Thread': 0}
                    label = f'injected-MemoryError-{fault}-{occurrence}'
                    error = None

                    def spawn(*args, **kwargs):
                        child = real_popen(*args, **kwargs)
                        children.append(child)
                        return child

                    def constructor(name, real):
                        def construct(*args, **kwargs):
                            calls[name] += 1
                            if name == fault and calls[name] == occurrence:
                                raise MemoryError(label)
                            instance = real(*args, **kwargs)
                            if name == 'Thread':
                                readers.append(instance)
                            return instance
                        return construct

                    try:
                        try:
                            with patch.object(subprocess, 'Popen', side_effect=spawn), \
                                    patch.object(runtime, 'Queue', new=constructor('Queue', real_queue)), \
                                    patch.object(runtime, 'Event', new=constructor('Event', real_event)), \
                                    patch.object(runtime, 'Thread', new=constructor('Thread', real_thread)):
                                runtime.stream_lines([sys.executable, '-c',
                                    'import time; time.sleep(60)'], lambda line: None, timeout=3)
                        except Exception as exc:
                            error = exc
                        # Snapshot anterior à limpeza do teste: nunca mascara o vazamento RED.
                        evidence = {
                            'phase': 'before_test_cleanup', 'fault': label,
                            'error_type': type(error).__name__, 'error': str(error),
                            'constructor_calls': calls, 'popen_count': len(children),
                            'children': [{'pid': child.pid, 'returncode': child.poll(),
                                          'stdout_closed': child.stdout.closed,
                                          'stderr_closed': child.stderr.closed}
                                         for child in children],
                            'reader_threads_alive': [reader.name for reader in readers if reader.is_alive()],
                            'control_pid': other.pid, 'control_returncode': other.poll(),
                            'control_pipes_open': not other.stdout.closed and not other.stderr.closed,
                            'control_threads_alive': [control.is_alive() for control in controls],
                        }
                        print('CONSTRUCTOR_EVIDENCE ' + json.dumps(evidence), flush=True)
                        self.assertIsInstance(error, MemoryError)
                        self.assertEqual(str(error), label)
                        self.assertIsNone(evidence['control_returncode'])
                        self.assertTrue(evidence['control_pipes_open'])
                        self.assertEqual(evidence['control_threads_alive'], [True, True])
                        self.assertEqual(evidence['reader_threads_alive'], [])
                        self.assertTrue(all(child['returncode'] is not None and
                            child['stdout_closed'] and child['stderr_closed']
                            for child in evidence['children']), evidence)
                    finally:
                        cleanup(children, readers)
                        print('CONSTRUCTOR_EVIDENCE ' + json.dumps({
                            'phase': 'after_test_cleanup', 'fault': label,
                            'children': [{'pid': child.pid, 'returncode': child.returncode,
                                          'stdout_closed': child.stdout.closed,
                                          'stderr_closed': child.stderr.closed}
                                         for child in children],
                            'reader_threads_alive': [reader.name for reader in readers if reader.is_alive()],
                            'control_returncode': other.poll(),
                            'control_threads_alive': [control.is_alive() for control in controls],
                        }), flush=True)
        finally:
            release.set()
            cleanup([] if other is None else [other], controls)


if __name__ == '__main__':
    unittest.main()
