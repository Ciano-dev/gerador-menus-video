"""Estado de autoria independente de Tk; nenhuma sondagem ou render nesta camada."""

from dataclasses import dataclass, field, replace
from fractions import Fraction
import math
from pathlib import Path
import re

from menu_video import ErroProjeto, font_size, numero, texto
from font_families import family_id


@dataclass(frozen=True)
class MediaInfo:
    source: Path
    duration: float


@dataclass
class Scene:
    source: Path
    source_duration: float
    name: str
    start: float = 0
    duration: float = 3
    font_size: int | None = None
    font_family: str | None = None

    def validate(self):
        font_size(self.font_size, 'Nome da cena')
        family_id(self.font_family, 'Nome da cena')
        texto(self.name, 'Nome da cena', 80)
        numero(self.start, 'Início da prévia', maximo=1e9)
        numero(self.duration, 'Duração da prévia', minimo=1/30)
        end = float(Fraction(str(self.start)) + Fraction(str(self.duration)))
        if self.start >= self.source_duration or end > self.source_duration:
            raise ErroProjeto('O trecho de prévia ultrapassa o vídeo; reduza início/duração.')


@dataclass
class Project:
    title: str = 'Meu menu de vídeos'
    scenes: list[Scene] = field(default_factory=list)
    colors: dict = field(default_factory=lambda: {
        'fundo': '#101f33', 'texto': '#f3ead6', 'destaque': '#c7a362'})
    duration_mode: str = 'manual'
    duration: float = 60
    music: MediaInfo | None = None
    show_footer: bool = True
    export_format: str = 'MP4'
    title_alignment: str = 'esquerda'
    title_font_size: int | None = None
    title_font_family: str | None = None

    def add_video(self, media):
        if len(self.scenes) >= 6:
            raise ErroProjeto('Selecione de 1 a 6 vídeos.')
        duration = min(3, math.floor(media.duration * 30 + 1e-7) / 30)
        scene = Scene(Path(media.source), media.duration, Path(media.source).stem[:80], duration=duration)
        scene.validate()
        self.scenes.append(scene)

    def edit_scene(self, index, name, start, duration):
        scene = replace(self.scenes[index], name=texto(name, 'Nome da cena', 80),
                        start=start, duration=duration)
        scene.validate()
        self.scenes[index] = scene

    def move_scene(self, index, offset):
        target = index + offset
        if not 0 <= index < len(self.scenes) or not 0 <= target < len(self.scenes):
            raise ErroProjeto('A cena já está no limite da lista.')
        self.scenes[index], self.scenes[target] = self.scenes[target], self.scenes[index]

    def remove_scene(self, index):
        del self.scenes[index]

    def effective_duration(self):
        if self.duration_mode == 'musica':
            if self.music is None:
                raise ErroProjeto('Escolha uma música para usar a duração da faixa.')
            return self.music.duration
        return self.duration

    def validate(self):
        font_size(self.title_font_size, 'Título')
        family_id(self.title_font_family, 'Título')
        texto(self.title, 'Título', 100)
        if self.export_format not in ('MP4', 'AVI'):
            raise ErroProjeto('Formato: escolha MP4 ou AVI.')
        if self.title_alignment not in ('esquerda', 'centralizado'):
            raise ErroProjeto('Título: escolha esquerda ou centralizado.')
        if not 1 <= len(self.scenes) <= 6:
            raise ErroProjeto('Selecione de 1 a 6 vídeos.')
        for scene in self.scenes:
            scene.validate()
        if (set(self.colors) != {'fundo', 'texto', 'destaque'} or
                any(not isinstance(c, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', c)
                    for c in self.colors.values())):
            raise ErroProjeto('Cores: use #RRGGBB para fundo, texto e destaque.')
        if type(self.show_footer) is not bool:
            raise ErroProjeto('Rodapé: valor booleano obrigatório.')
        if self.duration_mode not in ('manual', 'musica'):
            raise ErroProjeto('Duração: escolha manual ou música.')
        numero(self.effective_duration(), 'Duração do menu', minimo=1/30)
        if self.music:
            numero(self.music.duration, 'Música', minimo=1/30)
