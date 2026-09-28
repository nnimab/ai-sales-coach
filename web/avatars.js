'use strict';

// 客戶頭像：純 SVG 插畫，說話時 CSS 驅動嘴型與點頭，不需要 GPU
const AVATAR_ACCENT = { chen: '#5B8CFF', lin: '#E58AB0', wang: '#34D3BF' };

let avatarSeq = 0;

// square：填滿方形畫面（視訊框用）；預設為圓形頭像
function avatarSVG(kind, { square = false } = {}) {
  const u = `av${++avatarSeq}`;
  const p = AVATAR_PARTS[kind] || AVATAR_PARTS.chen;
  const clip = square ? '' : `clip-path="url(#${u}-clip)"`;
  return `<svg class="av-svg" viewBox="0 0 200 200" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs>
    <linearGradient id="${u}-bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="${p.bg[0]}"/><stop offset="1" stop-color="${p.bg[1]}"/>
    </linearGradient>
    <radialGradient id="${u}-skin" cx="0.45" cy="0.38" r="0.75">
      <stop offset="0" stop-color="${p.skin[0]}"/><stop offset="1" stop-color="${p.skin[1]}"/>
    </radialGradient>
    <clipPath id="${u}-clip"><circle cx="100" cy="100" r="100"/></clipPath>
  </defs>
  <g ${clip}>
    <rect x="-100" y="-100" width="400" height="400" fill="url(#${u}-bg)"/>
    ${p.hairBack || ''}
    <path d="M24 200 C28 168 58 151 100 151 C142 151 172 168 176 200 Z" fill="${p.body}"/>
    ${p.bodyDetail}
    <rect x="87" y="124" width="26" height="30" rx="9" fill="${p.skin[1]}"/>
    <g class="av-head">
      <ellipse cx="66" cy="101" rx="6" ry="10" fill="${p.skin[1]}"/>
      <ellipse cx="134" cy="101" rx="6" ry="10" fill="${p.skin[1]}"/>
      <ellipse cx="100" cy="96" rx="34" ry="41" fill="url(#${u}-skin)"/>
      ${p.blush ? `<ellipse cx="80" cy="112" rx="7" ry="4" fill="#F08A8A" opacity=".28"/><ellipse cx="120" cy="112" rx="7" ry="4" fill="#F08A8A" opacity=".28"/>` : ''}
      ${p.hairFront}
      <path d="M79 ${p.browY} q7 -${p.browArch} 14 0" stroke="${p.brow}" stroke-width="${p.browW}" fill="none" stroke-linecap="round"/>
      <path d="M107 ${p.browY} q7 -${p.browArch} 14 0" stroke="${p.brow}" stroke-width="${p.browW}" fill="none" stroke-linecap="round"/>
      <g class="av-eyes">
        <ellipse cx="86" cy="98" rx="3.7" ry="4.3" fill="#1C1C22"/>
        <ellipse cx="114" cy="98" rx="3.7" ry="4.3" fill="#1C1C22"/>
        <circle cx="87.3" cy="96.6" r="1.1" fill="#fff"/>
        <circle cx="115.3" cy="96.6" r="1.1" fill="#fff"/>
        ${p.lashes || ''}
      </g>
      ${p.glasses || ''}
      <path d="M100 102 q-3.5 7 0 9.5" stroke="${p.skin[2]}" stroke-width="2" fill="none" stroke-linecap="round"/>
      <g class="av-mouth">
        <path class="mouth-closed" d="M91 121 Q100 126.5 109 121" stroke="${p.lip}" stroke-width="2.6" fill="none" stroke-linecap="round"/>
        <g class="mouth-open">
          <ellipse cx="100" cy="122" rx="7.5" ry="5" fill="#6E2330"/>
          <ellipse cx="100" cy="124.6" rx="4.2" ry="2" fill="#C45A63"/>
        </g>
      </g>
      ${p.extra || ''}
    </g>
  </g>
</svg>`;
}

const AVATAR_PARTS = {
  chen: {
    bg: ['#23407A', '#0C1730'],
    skin: ['#F3CFAE', '#DDAA82', '#C98F6A'],
    body: '#2F4C7E',
    bodyDetail: `<path d="M80 152 L100 170 L120 152 L112 150 L100 160 L88 150 Z" fill="#E3E8F0"/>
      <path d="M100 170 L100 200" stroke="#27406B" stroke-width="2"/>`,
    hairFront: `<path d="M66 98 C60 80 60 60 74 50 C86 41 116 39 130 48 C142 56 142 78 136 98 L132 98 C132 86 130 76 124 70 C112 64 96 66 86 70 C78 73 72 80 70 98 Z" fill="#17181C"/>
      <path d="M86 70 C94 60 110 57 126 63" stroke="#fff" stroke-opacity=".08" stroke-width="3" fill="none"/>`,
    brow: '#1D1D22', browY: 87, browArch: 3, browW: 3.2,
    glasses: `<g fill="rgba(255,255,255,0.07)" stroke="#16171B" stroke-width="2.4">
        <rect x="73" y="89" width="25" height="17" rx="6"/><rect x="102" y="89" width="25" height="17" rx="6"/>
      </g>
      <path d="M98 96 h4 M73 94 L67 92 M127 94 L133 92" stroke="#16171B" stroke-width="2.2" stroke-linecap="round"/>`,
    lip: '#9C4A4A',
    extra: `<path d="M84 128 q16 8 32 0" stroke="#C98F6A" stroke-opacity=".25" stroke-width="5" fill="none" stroke-linecap="round"/>`,
  },
  lin: {
    bg: ['#6A2F55', '#27112A'],
    skin: ['#F5D6BF', '#E2B394', '#CC9476'],
    hairBack: `<path d="M58 106 C52 70 66 44 100 42 C134 44 148 70 142 106 C144 122 140 134 132 138 L68 138 C60 134 56 122 58 106 Z" fill="#4B4450"/>`,
    body: '#8A3F57',
    bodyDetail: `<path d="M86 152 L100 173 L114 152 Z" fill="#F4EAE6"/>
      <path d="M86 152 L100 173 M114 152 L100 173" stroke="#733247" stroke-width="3"/>
      <circle cx="100" cy="182" r="2.2" fill="#F1D9A0"/><circle cx="100" cy="192" r="2.2" fill="#F1D9A0"/>`,
    hairFront: `<path d="M66 94 C66 66 82 52 102 52 C120 52 134 62 136 90 C128 75 114 68 100 70 C88 72 76 81 66 94 Z" fill="#5A5260"/>
      <path d="M80 61 C90 56 104 55 116 59 M71 82 C77 71 85 65 95 63" stroke="#C4BCCB" stroke-opacity=".65" stroke-width="2" fill="none" stroke-linecap="round"/>`,
    brow: '#4A4048', browY: 86, browArch: 3.5, browW: 2.4,
    blush: true,
    glasses: `<g fill="none" stroke="#C9A25A" stroke-width="1.8">
        <circle cx="86" cy="98" r="9.5"/><circle cx="114" cy="98" r="9.5"/>
      </g>
      <path d="M95.5 97 q4.5 -3 9 0" stroke="#C9A25A" stroke-width="1.6" fill="none"/>`,
    lip: '#B85560',
    extra: `<circle cx="66" cy="113" r="3" fill="#F6F2EA"/><circle cx="134" cy="113" r="3" fill="#F6F2EA"/>`,
  },
  wang: {
    bg: ['#0F6363', '#062A30'],
    skin: ['#F8DAC6', '#E8B89C', '#D29A7C'],
    hairBack: `<path d="M56 100 C50 60 70 36 102 36 C134 36 152 62 146 102 C144 134 150 164 158 198 L42 198 C50 164 58 134 56 100 Z" fill="#2A1B16"/>`,
    body: '#2C3142',
    bodyDetail: `<path d="M86 152 L100 180 L114 152 Z" fill="#F1F3F7"/>
      <path d="M80 153 L100 186 L92 200 M120 153 L100 186 L108 200" stroke="#3A4056" stroke-width="3" fill="none"/>`,
    hairFront: `<path d="M64 96 C60 64 80 46 104 46 C126 46 142 62 138 94 C132 80 124 70 112 66 C100 75 82 85 64 96 Z" fill="#3A271F"/>
      <path d="M112 66 C104 60 92 58 82 62" stroke="#fff" stroke-opacity=".07" stroke-width="3" fill="none"/>`,
    brow: '#3A2A24', browY: 86, browArch: 4, browW: 2.3,
    blush: true,
    lashes: `<path d="M81.5 95 l-2.5 -2 M118.5 95 l2.5 -2" stroke="#1C1C22" stroke-width="1.6" stroke-linecap="round"/>`,
    lip: '#C8545E',
    extra: `<circle cx="66" cy="112" r="2.2" fill="#E8C46A"/><circle cx="134" cy="112" r="2.2" fill="#E8C46A"/>`,
  },
};
