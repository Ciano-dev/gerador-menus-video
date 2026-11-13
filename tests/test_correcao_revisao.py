"""Regressões reais dos dois erros da revisão de 21/10/2026."""
import json
import shutil
import struct
import sys
import tempfile
import unittest
import wave
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import menu_anisio as motor


class RevisionMediaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='anisio-revisao-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.ffmpeg = shutil.which('ffmpeg')
        self.ffprobe = shutil.which('ffprobe')
        self.source = self.base / 'fonte25.mp4'
        motor.run_media(motor.ffmpeg_base(self.ffmpeg) + [
            '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=25:duration=1',
            '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(self.source)])

    def project(self, name, *, start=.9, duration=.1, music=None, mode='manual'):
        data = {'modo': 'animado', 'titulo': 'Revisão',
                'duracao': {'modo': mode},
                'cenas': [{'arquivo': self.source.name, 'nome': 'Fonte 25fps',
                           'previa': {'inicio': start, 'duracao': duration}}]}
        if mode == 'manual':
            data['duracao']['segundos'] = 1/30 if music else .4
        if music:
            data['musica'] = {'arquivo': music.name}
        project = self.base / f'{name}.json'
        project.write_text(json.dumps(data), encoding='utf-8')
        return project

    def counted_video(self, path):
        result = motor.run_media([self.ffprobe, '-v', 'error', '-count_frames',
                                  '-select_streams', 'v:0', '-show_streams',
                                  '-of', 'json', str(path)])
        return json.loads(result.stdout)['streams'][0]

    def audio(self, samples):
        path = self.base / f'trilha-{samples}.wav'
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b''.join(struct.pack('<hh', *([1500 if i % 96 < 48 else -1500] * 2))
                                        for i in range(samples)))
        return path

    def test_wav1600_amostras_usa_duracao_racional_real(self):
        stream = motor.probe_media(self.ffprobe, self.audio(1600))['streams'][0]
        print('WAV_REAL', json.dumps(stream), flush=True)
        self.assertEqual(stream['duration_ts'], 1600)
        self.assertEqual(stream['time_base'], '1/48000')
        self.assertLess(float(stream['duration']), 1/30)
        self.assertEqual(motor.media_duration(stream, 'musica'), 1/30)

    def test_exporta_minimo_manual_e_musica_com1600_amostras(self):
        track = self.audio(1600)
        before = motor.sha256(track)
        for mode in ('manual', 'musica'):
            with self.subTest(modo=mode):
                output = self.base / f'entrega-{mode}.mp4'
                motor.export_project(self.project(mode, music=track, mode=mode), output, 'prova')
                catalog = output
                streams = motor.probe_media(self.ffprobe, catalog, count_frames=True)['streams']
                self.assertEqual([s['codec_type'] for s in streams], ['video', 'audio'])
                video, audio = streams
                self.assertEqual(int(video['nb_read_frames']), 1)
                self.assertEqual(Fraction(video['duration_ts']) * Fraction(video['time_base']), Fraction(1, 30))
                self.assertEqual((audio['codec_name'], audio['channels'], audio['sample_rate']),
                                 ('aac', 2, '48000'))
                audio_duration = Fraction(audio['duration_ts']) * Fraction(audio['time_base'])
                self.assertEqual(audio_duration, Fraction(1, 30))
                motor.check_decode(self.ffmpeg, catalog)
                pcm = motor.run_media(motor.ffmpeg_base(self.ffmpeg) + [
                    '-i', str(catalog), '-map', '0:a:0', '-f', 's16le',
                    '-ac', '2', '-ar', '48000', '-']).stdout
                self.assertEqual(len(pcm) // 4, 1600)
                self.assertTrue(any(value[0] for value in struct.iter_unpack('<h', pcm)))
                self.assertEqual(motor.sha256(track), before)
                self.assertTrue(output.is_file())
                print('EXPORTACAO_MINIMA_REAL', json.dumps({'modo': mode,
                      'video_frames': video['nb_read_frames'], 'video_duration': video['duration'],
                      'audio_duration': str(audio_duration), 'pcm_amostras': len(pcm) // 4,
                      'audio_codec': audio['codec_name'], 'sample_rate': audio['sample_rate'],
                      'duracao_esperada': 1/30}), flush=True)

    def test_wav1599_amostras_recusado_nos_dois_modos_sem_publicacao(self):
        track = self.audio(1599)
        stream = motor.probe_media(self.ffprobe, track)['streams'][0]
        self.assertEqual(stream['duration_ts'], 1599)
        self.assertLess(motor.media_duration(stream, 'musica'), 1/30)
        for mode in ('manual', 'musica'):
            with self.subTest(modo=mode):
                output = self.base / f'curta-{mode}.mp4'
                with self.assertRaisesRegex(motor.ErroProjeto, 'menor que um frame'):
                    motor.export_project(self.project(mode, music=track, mode=mode), output, 'prova')
                self.assertFalse(output.exists())
                self.assertFalse(list(self.base.glob(f'.{output.name}*')))
                print('WAV1599_RECUSADO', mode, stream['duration_ts'], stream['time_base'], flush=True)

    def test_segmento25fps_no_fim_confere_frames_antes_do_loop_e_manifesto(self):
        observed = []
        previews = []
        run = motor.run_media
        def observe(arguments, **kwargs):
            result = run(arguments, **kwargs)
            if str(arguments[-1]).startswith('trecho-'):
                stream = self.counted_video(Path(kwargs['cwd']) / arguments[-1])
                observed.append(stream)
            return result
        output = self.base / 'entrega.mp4'
        render = motor.render_animated
        def capture(*args):
            previews.append(dict(args[4][0]['previa']))
            return render(*args)
        with patch.object(motor, 'run_media', side_effect=observe), patch.object(motor, 'render_animated', side_effect=capture):
            motor.export_project(self.project('fim'), output, 'prova')
        preview = previews[0]
        self.assertEqual(len(observed), 1)
        stream = observed[0]
        print('SEGMENTO_REAL', json.dumps({'frames': stream['nb_read_frames'],
              'duration': stream['duration'], 'duration_ts': stream['duration_ts'],
              'time_base': stream['time_base'], 'manifesto': preview}), flush=True)
        self.assertEqual(int(stream['nb_read_frames']), 3)
        self.assertAlmostEqual(float(stream['duration']), preview['duracao_efetiva'], places=6)


class MediaDurationTests(unittest.TestCase):
    def test_racional_valido_tem_prioridade_sobre_decimal_ou_ausencia(self):
        for duration in ('0.033333', '2', 'N/A', 'nan', None):
            with self.subTest(duration=duration):
                stream = {'duration_ts': 1600, 'time_base': '1/48000'}
                if duration is not None:
                    stream['duration'] = duration
                self.assertEqual(motor.media_duration(stream, 'musica'), 1/30)

    def test_racional_invalido_ou_ausente_usa_decimal_valido(self):
        for fields in ({}, {'duration_ts': 1600}, {'time_base': '1/48000'},
                       {'duration_ts': 'N/A', 'time_base': '1/48000'},
                       {'duration_ts': 0, 'time_base': '1/48000'},
                       {'duration_ts': -1600, 'time_base': '-1/48000'},
                       {'duration_ts': True, 'time_base': '1/48000'},
                       {'duration_ts': '1599.5', 'time_base': '1/48000'},
                       {'duration_ts': 'nan', 'time_base': '1/48000'},
                       {'duration_ts': 'inf', 'time_base': '1/48000'},
                       {'duration_ts': 1600, 'time_base': 'inf'},
                       {'duration_ts': 1600, 'time_base': 'nan'},
                       {'duration_ts': 1600, 'time_base': True},
                       {'duration_ts': 1600, 'time_base': '1e1000'},
                       {'duration_ts': 1600, 'time_base': '1e-1000'},
                       {'duration_ts': 1600, 'time_base': '1/0'},
                       {'duration_ts': 1600, 'time_base': '0/48000'},
                       {'duration_ts': 1600, 'time_base': '-1/48000'},
                       {'duration_ts': 1600, 'time_base': None}):
            with self.subTest(fields=fields):
                self.assertEqual(motor.media_duration(dict(fields, duration='0.5'), 'musica'), .5)

    def test_sem_duracao_finita_positiva_recusa(self):
        for duration in (None, 'N/A', 'nan', 'inf', '-inf', '0', '-1', True, False,
                         float('nan'), float('inf'), 0, -1):
            with self.subTest(duration=duration):
                stream = {'duration': duration, 'duration_ts': 1600, 'time_base': '1/0'}
                with self.assertRaisesRegex(motor.ErroProjeto, 'duração do stream'):
                    motor.media_duration(stream, 'musica')


if __name__ == '__main__':
    unittest.main()
