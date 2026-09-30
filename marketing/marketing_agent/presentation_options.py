"""Explicit frontend requirements; separate from the immutable manuscript."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .presentation_styles import get_style


class PresentationOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    style_id: str = 'auto'
    template_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    template_revision: int | None = Field(default=None, ge=1, strict=True)
    design_prompt: str = Field(default='', max_length=12000)
    page_prompt: str = Field(default='', max_length=4000)
    aspect_ratio: Literal['16:9'] = '16:9'
    page_count_min: int | None = Field(default=None, ge=3, le=45, strict=True)
    page_count_max: int | None = Field(default=None, ge=3, le=45, strict=True)
    palette: list[Annotated[str, Field(pattern=r'^#[0-9a-fA-F]{6}$')]] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def validate_options(self):
        get_style(self.style_id)
        if (self.template_id is None) != (self.template_revision is None):
            raise ValueError('企业模板ID与发布版本必须同时提供')
        if self.template_id and (self.palette or self.design_prompt or self.page_prompt):
            raise ValueError('企业模板使用已发布的品牌样式，不接受自由配色或设计提示')
        if (self.page_count_min is None) != (self.page_count_max is None):
            raise ValueError('页数上下限必须同时提供')
        if self.page_count_min is not None and self.page_count_min > self.page_count_max:
            raise ValueError('页数下限不能大于上限')
        return self

    def requirements(self):
        return self.model_dump(exclude={'style_id'}, exclude_defaults=True)


def check_page_count(count, options):
    minimum, maximum = options.get('page_count_min'), options.get('page_count_max')
    if minimum is not None and not minimum <= count <= maximum:
        raise ValueError(f'生成页数 {count} 不符合用户要求 {minimum}–{maximum} 页，请调整页数或重新生成')


def apply_user_palette(theme, options):
    palette = options.get('palette', [])
    if not palette:
        return theme
    def luminance(color):
        channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        return sum(c * w for c, w in zip(linear, (.2126, .7152, .0722)))
    background = palette[-1]
    text = '141414' if luminance(background) > .179 else 'FFFFFF'
    return {**theme, 'background': background[1:], 'text': text, 'accent': palette[0][1:], 'muted': text, 'palette': palette}
