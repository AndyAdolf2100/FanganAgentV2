export type TemplateTextRole = 'title' | 'subtitle' | 'body' | 'itemTitle' | 'itemBody' | 'contentsItem' | 'contentsNumber' | 'contentsSubtitle' | 'sectionNumber' | 'pageNumber' | 'date' | 'presenter' | 'brand' | 'decoration';
export type TemplatePageRole = 'cover' | 'contents' | 'section' | 'body' | 'ending' | 'exclude';
export interface TemplateTextRun {
  text: string;
  size: number;
  font: string;
  sourceFont?: string;
  color: string;
  bold: boolean;
  opacity?: number;
  shadow?: { color: string; opacity: number; blur: number; x: number; y: number };
}
export interface TemplateElement {
  id?: string;
  textRole?: TemplateTextRole;
  order?: number;
  labelSource?: 'rule' | 'model' | 'manual';
  confidence?: number;
  matrix?: [number, number, number, number];
  artworkType?: 'outlinedText';
  artworkTextRole?: 'title' | 'subtitle' | 'sectionNumber' | 'body' | 'brand' | 'decoration';
  fillSource?: 'group';
  kind: 'image' | 'text' | 'shape';
  binding?: 'fixed' | 'content' | 'pageNumber';
  slotId?: string;
  fieldName?: string;
  textGroupId?: string;
  textPartIndex?: number;
  x: number;
  y: number;
  width: number;
  height: number;
  rotation?: number;
  asset?: string;
  renderAsset?: string;
  imageColorChange?: { from: string; to: string; opacity: number };
  crop?: { left: number; top: number; right: number; bottom: number };
  vector?: { width: number; height: number; paths: string[]; strokeGradient?: { angle: number; stops: { offset: number; color: string; opacity: number }[] }; gradient?: { angle: number; stops: { offset: number; color: string; opacity: number }[] } };
  fillOpacity?: number;
  cornerRadius?: number;
  strokeWidth?: number;
  strokeOpacity?: number;
  blur?: number;
  glow?: { color: string; opacity: number; radius: number };
  shadow?: { color: string; opacity: number; blur: number; x: number; y: number; scaleX: number; scaleY: number; skewX: number; skewY: number; alignX: number; alignY: number };
  imageFlip?: { horizontal: boolean; vertical: boolean };
  imageClip?: { width: number; height: number; paths: string[] };
  clipPolygon?: [number, number][];
  imageStretch?: { left: number; top: number; right: number; bottom: number };
  fill?: string;
  stroke?: string;
  geometry?: 'rect' | 'ellipse' | 'roundRect';
  textFlip?: { horizontal: boolean; vertical: boolean };
  textTransformVersion?: number;
  textLayout?: { fontScale?: number; wrap: boolean; vertical: 'top' | 'center' | 'bottom'; padding: [number, number, number, number]; overflow: boolean };
  paragraphs?: { lineHeight?: number; align: 'left' | 'center' | 'right'; runs: TemplateTextRun[] }[];
  placeholder?: string;
  fixed?: boolean;
}
export interface EnterpriseTemplatePage {
  layoutKind?: 'auto' | 'text' | 'items' | 'comparison' | 'timeline' | 'table' | 'custom';
  labelRuleVersion?: number;
  id?: string;
  labelSource?: 'rule' | 'model' | 'manual';
  confidence?: number;
  name: string;
  role: TemplatePageRole;
  background: string;
  backgroundPattern?: { type: 'diagBrick'; foreground: string; background: string };
  elements: TemplateElement[];
}
export interface EnterpriseTemplate {
  version: 1;
  published?: { componentSchema?: 1; revision: number; name: string; pages: EnterpriseTemplatePage[] };
  id: string;
  name: string;
  createdAt: number;
  width: number;
  height: number;
  originalWidth: number;
  originalHeight: number;
  aspectRatio: string;
  pages: EnterpriseTemplatePage[];
  assets: { id: string; mime: string; data: string; derivedKey?: string }[];
  fonts: string[];
  colors: string[];
  warnings: string[];
}
