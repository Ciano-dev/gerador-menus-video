"""Evidência Linux FFmpeg real; frames exportados não são screenshots da GUI."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui_autoria import Project
import gui_servicos as services
import menu_anisio as motor
from native_render_checks import tool_identity


def run_matrix(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report = {'success': False, 'kind': 'actual exported video frames; not GUI screenshots',
              'platform': sys.platform, 'cases': [], 'errors': []}
    started = time.monotonic()
    commands = []
    original_run = motor.run_media
    active_case = None

    def write():
        (directory/'geometry.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        (directory/'commands.json').write_text(json.dumps(commands, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    def observe(command, **kwargs):
        command = list(map(str, command))
        row = {'command': command, 'cwd': str(kwargs.get('cwd', Path.cwd())), 'exit': None}
        commands.append(row)
        index = len(commands)
        if '-filter_complex' in command and active_case is not None:
            graph = command[command.index('-filter_complex')+1]
            active_case['graph'] = graph
            work = Path(kwargs['cwd'])
            active_case['title_lines'] = []
            for match in re.finditer(r'textfile=(titre(?:-line\d+)?\.txt):[^,;]*fontsize=(\d+):[^,;]*:x=([^:]+):y=(\d+)', graph):
                filename, size, x, y = match.groups()
                active_case['title_lines'].append({'text': (work/filename).read_text(encoding='utf-8'),
                                                  'font_size': int(size), 'draw_x': x, 'draw_y': int(y)})
        tick = time.monotonic()
        try:
            result = original_run(command, **kwargs)
            row['exit'] = result.returncode
            # Raw frame output is an artifact below, not a terminal log.
            if '-f' not in command or command[command.index('-f')+1] != 'rawvideo':
                (directory/f'{index:04d}-stdout.log').write_bytes(result.stdout)
            (directory/f'{index:04d}-stderr.log').write_bytes(result.stderr)
            return result
        except Exception as exc:
            row['error'] = str(exc)
            row['exit'] = getattr(exc.__cause__, 'returncode', None)
            raise
        finally:
            row['elapsed_seconds'] = time.monotonic()-tick
            write()

    motor.run_media = observe
    try:
        report['identity'] = tool_identity()
        ffmpeg, ffprobe = motor.resolve_tool('ffmpeg'), motor.resolve_tool('ffprobe')
        font = motor.find_font(None)
        report['font'] = {'path': str(font), 'before': motor.sha256(font)} if font else None
        source, music = directory/'source.mp4', directory/'music.wav'
        motor.run_media(motor.ffmpeg_base(ffmpeg)+['-f','lavfi','-i','testsrc2=s=320x180:r=30:d=0.4',
            '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(source)])
        motor.run_media(motor.ffmpeg_base(ffmpeg)+['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.4',str(music)])
        project = Project(title='Título', duration=.4)
        project.add_video(services.inspect_media(source))
        project.edit_scene(0, 'Cena preservada', 0, .2)
        project.music = services.inspect_media(music, audio=True)
        saved = services.save_project(project, directory/'original.menu')
        protected = [source, music, saved]
        originals = {str(p): motor.sha256(p) for p in protected}
        report['protected_before'] = originals.copy()
        lower_graphs = {}
        for export_format in ('MP4','AVI'):
            for alignment in ('esquerda','centralizado'):
                for multiline in (False, True):
                    name = f'{export_format.lower()}-{alignment}-'+('multi' if multiline else 'single')
                    case_dir = directory/name
                    case_dir.mkdir()
                    active_case = {'name': name, 'format': export_format, 'alignment': alignment,
                                   'multiline': multiline, 'exit': None}
                    report['cases'].append(active_case)
                    candidate = copy.deepcopy(project)
                    candidate.export_format, candidate.title_alignment = export_format, alignment
                    candidate.title = ' '.join(['MMMM']*18) if multiline else 'João e Lívia'
                    output = services.render_project(candidate,None,case_dir/('menu.'+export_format.lower()),profile='1080p')
                    streams = motor.probe_media(ffprobe,output,count_frames=True)['streams']
                    video = next(s for s in streams if s['codec_type']=='video')
                    assert (video['width'],video['height'],video['pix_fmt'],video['r_frame_rate']) == (1920,1080,'yuv420p','30/1')
                    assert video['codec_name'] == ('h264' if export_format=='MP4' else 'mpeg4')
                    assert int(video['nb_read_frames']) == 12
                    motor.check_decode(ffmpeg,output)
                    frame = motor.run_media(motor.ffmpeg_base(ffmpeg)+['-i',str(output),'-frames:v','1',
                        '-pix_fmt','rgb24','-threads','1','-f','rawvideo','-']).stdout
                    assert len(frame)==1920*1080*3
                    raw = case_dir/'frame.rgb'
                    raw.write_bytes(frame)
                    png = case_dir/'frame.png'
                    motor.run_media(motor.ffmpeg_base(ffmpeg)+['-i',str(output),'-frames:v','1','-threads','1',str(png)])
                    rows, pixels = [], {}
                    for y in range(198):
                        xs = [x for x in range(1920) if frame[(y*1920+x)*3]>180 and
                              frame[(y*1920+x)*3+1]>170 and frame[(y*1920+x)*3+2]>150]
                        if xs:
                            rows.append(y); pixels[y]=xs
                    assert rows, 'No actual title pixels found'
                    bands=[]
                    for y in rows:
                        if not bands or y>bands[-1][-1]+1: bands.append([y])
                        else: bands[-1].append(y)
                    boxes=[]
                    for band in bands:
                        xs=[x for y in band for x in pixels[y]]
                        boxes.append([min(xs),band[0],max(xs)+1,band[-1]+1])
                    top,bottom=rows[0],rows[-1]+1
                    top_gap,bottom_gap=top,198-bottom
                    active_case.update(output=str(output),output_sha256=motor.sha256(output),
                        frame_png=str(png),frame_sha256=motor.sha256(png),raw_sha256=motor.sha256(raw),
                        streams=streams,full_decode=True,actual_title_boxes=boxes,
                        top=top,bottom=bottom,top_gap=top_gap,bottom_gap=bottom_gap,
                        center_error=(top+bottom)/2-99,
                        actual_interline_gaps=[b[1]-a[3] for a,b in zip(boxes,boxes[1:])])
                    assert abs(top_gap-bottom_gap)<=1, f'Actual raster not centered: {top_gap}, {bottom_gap}'
                    assert len(boxes)==len(active_case['title_lines'])==(2 if multiline else 1)
                    assert all(gap>0 and gap<20 for gap in active_case['actual_interline_gaps'])
                    for box, line in zip(boxes, active_case['title_lines']):
                        if alignment=='centralizado':
                            assert line['draw_x']=='(w-text_w)/2'
                            assert abs((box[0]+box[2])/2-960)<=3
                        else:
                            assert line['draw_x']=='88'
                            # Glyph bearings differ (M starts five pixels after its origin).
                            assert 85 <= box[0] <= 88+line['font_size']/2
                    lower_graph = re.sub(r'drawtext=[^,;]*textfile=titre[^,;]*,?', '', active_case['graph'])
                    if export_format in lower_graphs: assert lower_graphs[export_format]==lower_graph
                    else: lower_graphs[export_format]=lower_graph
                    assert all(motor.sha256(Path(p))==value for p,value in originals.items())
                    originals[str(output)]=motor.sha256(output)
                    active_case['exit']=0
                    write()
                    print(f'{name}: exit=0, actual bbox y={top}..{bottom}, gaps={top_gap}/{bottom_gap}',flush=True)
        report['protected_after']={str(p):motor.sha256(p) for p in protected}
        assert report['protected_before']==report['protected_after']
        if font:
            report['font']['after']=motor.sha256(font)
            assert report['font']['before']==report['font']['after']
        report['earlier_exports_preserved']=True
        report['divider_cards_numbers_scene_labels_palette_graph_preserved']=True
        report['success']=True
    except Exception:
        report['errors'].append(traceback.format_exc())
    finally:
        motor.run_media=original_run
        report['elapsed_seconds']=time.monotonic()-started
        write()
    return 0 if report['success'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    raise SystemExit(run_matrix(parser.parse_args().output))
