"""Fatias verticais do menu animado; mídia e pixels reais, sem dependências."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import menu_anisio as motor
from gerar_fixtures import generate


def wav(path, duration, rate=8000):
    import math
    import struct
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        second = b''.join(struct.pack('<h', round(2000 * math.sin(2*math.pi*220*i/rate)))
                          for i in range(rate))
        stream.writeframes((second * math.ceil(duration))[:round(duration*rate)*2])


class AnimatedValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='animado-validacao-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        generate(cls.base / 'fontes')

    def setUp(self):
        self.project = self.base / 'fontes' / 'animado.json'
        self.output = self.base / f'{self._testMethodName}.mp4'
        self.data = {'modo': 'animado', 'titulo': 'João 100% %{x}: [a];',
                     'duracao': {'modo': 'manual', 'segundos': 2},
                     'cenas': [{'arquivo': 'cores-com-audio.mp4', 'nome': 'Cerimônia',
                                'previa': {'inicio': 0, 'duracao': .5}}]}

    def write(self):
        self.project.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')

    def reject(self, message):
        self.write()
        with self.assertRaisesRegex(motor.ErroProjeto, message):
            motor.export_project(self.project, self.output, 'prova')
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob(f'.{self.output.name}*')))

    def test_modo_desconhecido_recusado(self):
        self.data['modo'] = 'surpresa'
        self.reject('modo')

    def test_parametros_finitos_limites_e_seis_cenas(self):
        original = copy.deepcopy(self.data)
        for value in [-1, 0, True, '3', float('inf'), float('nan')]:
            with self.subTest(segundos=value):
                self.data = copy.deepcopy(original)
                self.data['duracao']['segundos'] = value
                self.reject('duracao|JSON')
        for field, values in [('inicio', [-1, True, '0', float('inf')]),
                              ('duracao', [-1, 0, True, '1', float('nan')])]:
            for value in values:
                with self.subTest(field=field, value=value):
                    self.data = copy.deepcopy(original)
                    self.data['cenas'][0]['previa'][field] = value
                    self.reject('previa|JSON')
        self.data = copy.deepcopy(original)
        self.data['cenas'] *= 7
        self.reject('1 a 6')

    def test_trecho_fora_da_fonte_recusado_sem_pasta(self):
        for start, duration in [(1, .2), (.8, .5)]:
            with self.subTest(start=start):
                self.data['cenas'][0]['previa'] = {'inicio': start, 'duracao': duration}
                self.reject('trecho|prévia')


def pixels(path, time, crop=None):
    command = motor.ffmpeg_base(shutil.which('ffmpeg')) + ['-ss', str(time), '-i', str(path),
               '-map', '0:V:0', '-frames:v', '1']
    if crop:
        command += ['-vf', f'crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}']
    return motor.run_media(command + ['-pix_fmt', 'rgb24', '-threads', '1', '-f', 'rawvideo', '-']).stdout


def difference(a, b):
    return sum(abs(x-y) for x, y in zip(a, b)) / len(a)


class AnimatedRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='animado-render-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        cls.source = cls.base / 'filme.mp4'
        motor.run_media(motor.ffmpeg_base(shutil.which('ffmpeg')) + [
            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=5',
            '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(cls.source)])

    def project(self, count=1):
        path = self.base / f'{self._testMethodName}.json'
        path.write_text(json.dumps({'modo': 'animado', 'titulo': 'João 100% %{eif:1:d} [a];',
            'duracao': {'modo': 'manual', 'segundos': 4},
            'cenas': [{'arquivo': self.source.name, 'nome': 'Cerimônia: 100% [x];',
                       'previa': {'inicio': 1, 'duracao': 1.5}} for _ in range(count)]},
            ensure_ascii=False), encoding='utf-8')
        return path

    def test_uma_cena_duracao_movimento_loop_e_textos_estaveis(self):
        output = self.base / 'um-item.mp4'
        before = motor.sha256(self.source)
        motor.export_project(self.project(), output, 'prova')
        catalog = output
        info = motor.probe_media(shutil.which('ffprobe'), catalog)
        self.assertAlmostEqual(float(info['format']['duration']), 4, delta=.04)
        self.assertEqual(len(info['streams']), 1)
        self.assertTrue(output.is_file())
        self.assertEqual(before, motor.sha256(self.source))
        self.assertFalse((self.base / 'manifesto.json').exists())
        # Tolerância média RGB: H264 CRF20 pode alterar bordas estáticas em até 3 níveis.
        self.assertLess(difference(pixels(catalog, .2, (0, 0, 640, 70)),
                                   pixels(catalog, 1, (0, 0, 640, 70))), 3)
        a, b, c = [pixels(catalog, t) for t in [.2, .8, 1.7]]
        self.assertGreater(difference(a, b), .3)
        self.assertLess(difference(a, c), .5)

    def test_seis_cenas_movem_simultaneamente_sem_abertura(self):
        project = self.project(6)
        portrait = self.base / 'retrato.mp4'
        motor.run_media(motor.ffmpeg_base(shutil.which('ffmpeg')) + [
            '-f', 'lavfi', '-i', 'testsrc2=size=90x160:rate=30:duration=5',
            '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(portrait)])
        data = json.loads(project.read_text(encoding='utf-8'))
        data['cenas'][5]['arquivo'] = portrait.name
        project.write_text(json.dumps(data), encoding='utf-8')
        output = self.base / 'seis-itens.mp4'
        motor.export_project(project, output, 'prova')
        catalog = output
        for cell in motor.animated_layout('prova'):
            crop = (cell['x']+cell['pad'], cell['y']+cell['pad'], cell['vw'], cell['vh'])
            a, b, c = [pixels(catalog, t, crop) for t in [.2, .8, 1.7]]
            self.assertGreater(difference(a, b), 2, 'todos os seis itens devem mover juntos')
            # Duas compressões CRF18/20: tolerância média de 4 níveis RGB no recorte.
            self.assertLess(difference(a, c), 4, 'loop só do segmento com início em 1s')
            self.assertLess(difference(a, c), difference(a, b)*.3)
        # Compara com a filmagem no instante absoluto 1.2s: prova início não zero.
        cell = motor.animated_layout('prova')[0]
        expected = motor.run_media(motor.ffmpeg_base(shutil.which('ffmpeg')) + [
            '-ss', '1.2', '-i', str(self.source), '-frames:v', '1', '-vf',
            f"scale={cell['vw']}:{cell['vh']}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1,format=yuv420p,setsar=1,pad={cell['vw']}:{cell['vh']}:(ow-iw)/2:(oh-ih)/2:color=0x101f33",
            '-pix_fmt', 'rgb24', '-threads', '1', '-f', 'rawvideo', '-']).stdout
        actual = pixels(catalog, .2, (cell['x']+cell['pad'], cell['y']+cell['pad'], cell['vw'], cell['vh']))
        self.assertLess(difference(actual, expected), 8, 'tolerância RGB para duas compressões H264')

    def test_filme_com_capa_usa_video_verdadeiro(self):
        from test_streams import create_media
        source, _ = create_media(self.base / 'capa')
        project = self.project()
        data = json.loads(project.read_text(encoding='utf-8'))
        data['cenas'][0].update(arquivo=str(source.relative_to(self.base)),
                                previa={'inicio': 0, 'duracao': .5})
        project.write_text(json.dumps(data), encoding='utf-8')
        output = self.base / 'capa-entrega.mp4'
        motor.export_project(project, output, 'prova')
        cell = motor.animated_layout('prova')[0]
        raw = pixels(output, .2,
                     (cell['x']+cell['pad']+10, cell['y']+cell['pad']+10, 20, 20))
        self.assertGreater(sum(raw[2::3])/400, 235)
        self.assertLess(sum(raw[0::3])/400, 12)

    def test_falha_de_render_e_mutacao_nao_publicam_pasta_parcial(self):
        project = self.project()
        output = self.base / 'falha.mp4'
        sentinel = self.base / '.desconhecido'
        sentinel.write_text('preservar', encoding='utf-8')
        with patch.object(motor, 'render_animated', side_effect=motor.ErroProjeto('induzida')):
            with self.assertRaisesRegex(motor.ErroProjeto, 'induzida'):
                motor.export_project(project, output, 'prova')
        self.assertFalse(output.exists())
        self.assertFalse(list(self.base.glob('.falha*')))
        render = motor.render_animated
        def tamper(*args):
            render(*args)
            import os
            source = args[4][0]['source']
            info = source.stat()
            os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
        with patch.object(motor, 'render_animated', side_effect=tamper):
            with self.assertRaisesRegex(motor.ErroProjeto, 'mudou'):
                motor.export_project(project, output, 'prova')
        self.assertFalse(output.exists())
        self.assertFalse(list(self.base.glob('.falha*')))
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'preservar')

    def test_overwrite_lock_e_scratch_tmpdir(self):
        import os
        project = self.project()
        output = self.base / 'reservada.mp4'
        lock = self.base / '.reservada.mp4.anisio.lock'
        lock.write_text('outro processo', encoding='utf-8')
        with self.assertRaisesRegex(motor.ErroProjeto, 'reservou'):
            motor.export_project(project, output, 'prova')
        self.assertEqual(lock.read_text(encoding='utf-8'), 'outro processo')
        scratch = self.base / 'scratch-custom'
        scratch.mkdir()
        target = self.base / 'scratch-entrega.mp4'
        render = motor.render_animated
        def observe(*args):
            self.assertEqual(args[1].parent, scratch)
            render(*args)
        with patch.object(tempfile, 'tempdir', str(scratch)), patch.object(motor, 'render_animated', side_effect=observe):
            motor.export_project(project, target, 'prova')
        self.assertEqual(list(scratch.iterdir()), [])
        before = motor.sha256(target)
        with self.assertRaisesRegex(motor.ErroProjeto, 'saída já existe'):
            motor.export_project(project, target, 'prova')
        self.assertEqual(before, motor.sha256(target))


class AnimatedAudioTests(AnimatedValidationTests):
    def test_musica_curta_continua_com_silencio_sem_loop(self):
        wav(self.base / 'fontes' / 'curta.wav', 1)
        self.data['musica'] = {'arquivo': 'curta.wav'}
        self.write()
        motor.export_project(self.project, self.output, 'prova')
        audio = motor.probe_media(motor.resolve_tool('ffprobe'), self.output)['streams'][1]
        self.assertAlmostEqual(motor.media_duration(audio, 'música'), 2, places=3)

    def test_trilha_invalida_ou_remota_recusada(self):
        for filename, message in [('retrato-mudo.mov', 'áudio'),
                                  ('https://example.org/som.wav', 'local|relativo'),
                                  ('C:\\musica.wav', 'local|relativo')]:
            with self.subTest(filename=filename):
                self.data['musica'] = {'arquivo': filename}
                self.reject(message)

    def test_duracao_pela_musica_audio_unico_estereo_e_fade(self):
        track = self.base / 'fontes' / 'trilha.wav'
        wav(track, 2.5)
        before = motor.sha256(track)
        self.data['musica'] = {'arquivo': track.name}
        self.data['duracao'] = {'modo': 'musica'}
        self.write()
        motor.export_project(self.project, self.output, 'prova')
        catalog = self.output
        streams = motor.probe_media(shutil.which('ffprobe'), catalog)['streams']
        self.assertEqual([s['codec_type'] for s in streams], ['video', 'audio'])
        self.assertEqual(streams[1]['codec_name'], 'aac')
        self.assertEqual(streams[1]['channels'], 2)
        self.assertAlmostEqual(float(streams[0]['duration']), 2.5, delta=.04)
        self.assertEqual(before, motor.sha256(track))
        self.assertTrue(self.output.is_file())
        raw = motor.run_media(motor.ffmpeg_base(shutil.which('ffmpeg')) + [
            '-i', str(catalog), '-map', '0:a:0', '-f', 's16le', '-ac', '1', '-ar', '8000', '-']).stdout
        import array
        samples = array.array('h', raw)
        def rms(start, end):
            part = samples[round(start*8000):round(end*8000)]
            return (sum(x*x for x in part)/len(part))**.5
        self.assertLess(rms(2.42, 2.48), rms(.5, 1) * .2)


if __name__ == '__main__':
    unittest.main()
