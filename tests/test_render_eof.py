"""Contrato de EOF do comando emitido pelo motor e rounding real a 30 fps."""
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import menu_video as motor


class BackgroundEOFTests(unittest.TestCase):
    def capture(self, directory, seconds):
        commands = []
        with patch.object(motor, 'run_media', side_effect=lambda command, **kw: commands.append(command)), \
                patch.object(motor, 'measure_text_pixels', return_value=(10, 10)):
            motor.render_animated('ffmpeg', directory, directory, 'EOF', [], 'prova', False,
                                  {'visual': {'fundo': '#101f33', 'texto': '#f3ead6',
                                              'destaque': '#c7a362'},
                                   'segundos': seconds, 'musica': None})
        self.assertEqual(len(commands), 1)
        return commands[0]

    def test_engine_command_has_finite_background_and_keeps_final_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            for seconds in (1/30, .117, 15, 30):
                with self.subTest(seconds=seconds):
                    command = self.capture(Path(tmp), seconds)
                    color = command[command.index('-i') + 1]
                    options = dict(part.split('=', 1) for part in color.split(':'))
                    self.assertIn('d', options, 'input color infinito: falta duração EOF explícita')
                    self.assertEqual(float(options['d']), seconds)
                    self.assertEqual(command[command.index('-t') + 1], str(seconds))
                    self.assertEqual(options['r'], '30')

    def test_real_frame_rounding_minimum_fractional_and_15_30_seconds(self):
        ffmpeg, ffprobe = motor.resolve_tool('ffmpeg'), motor.resolve_tool('ffprobe')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source = directory / 'source.mp4'
            motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
                'testsrc2=size=160x90:rate=30:duration=0.2', '-an', '-c:v', 'libx264',
                '-threads', '1', '-pix_fmt', 'yuv420p', str(source)])
            # FFmpeg -t arredonda o limite ao frame mais próximo; .117*30=3.51 => 4.
            for seconds, frames in ((1/30, 1), (.117, 4), (15, 450), (30, 900)):
                with self.subTest(seconds=seconds):
                    output = directory / f'frames-{frames}.mp4'
                    motor.export_media('EOF', [{'source': source, 'nome': 'Cena',
                        'previa': {'inicio': 0, 'duracao': .1}}], output,
                        {'segundos': seconds, 'modo_duracao': 'manual', 'musica': None,
                         'visual': {'fundo': '#101f33', 'texto': '#f3ead6', 'destaque': '#c7a362'}},
                        'prova')
                    video = motor.probe_media(ffprobe, output, count_frames=True)['streams'][0]
                    self.assertEqual(int(video['nb_read_frames']), frames)
                    # MP4 edit lists usam timescale de 1000; o último pacote
                    # fracionário pode perder menos de 1 ms, nunca um frame.
                    self.assertLess(abs(Fraction(video['duration_ts']) * Fraction(video['time_base'])
                                        - Fraction(frames, 30)), Fraction(1, 1000))
