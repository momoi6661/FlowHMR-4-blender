# VRM preview dependencies

FlowHMR loads local VRM 0.x and 1.0 avatars with these pinned files. The preview uses the same Three.js r160 build as the FBX and PMX loaders.

| File | Upstream version | SHA-256 |
| --- | --- | --- |
| `lib/three-vrm.module.min.js` | [@pixiv/three-vrm 3.5.5](https://www.npmjs.com/package/@pixiv/three-vrm/v/3.5.5) | `99f943e956e52dbf52bbe418899e8a8ae5e35f5c79a94ab8e404926afc620bf2` |
| `loaders/GLTFLoader.js` | [Three.js r160](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/loaders/GLTFLoader.js) | `d073b438e6a07e1359741dd5d6c76c953420cc0d4fd84eb1bdde94315540e6a3` |
| `utils/BufferGeometryUtils.js` | [Three.js r160](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/utils/BufferGeometryUtils.js) | `9be041e96308775d00e2695cc607645b9a9b64fd7c0e759dd8f7c00a8d92becb` |

The original [three-vrm MIT license](LICENSE) and [Three.js MIT license](../LICENSE) accompany these files.

The custom retargeter uses [GLTFExporter](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/exporters/GLTFExporter.js) and [TextureUtils](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/utils/TextureUtils.js) from the same MIT-licensed r160 revision to bake a GLB offline. Original SHA-256: exporter `f8a6eb87719e68c925fdf445014b5ead49cf779ae14e69c6cc1cb1dd4eae514d` (local import path adapted), utility `1b84a7fde8745dd614f62b6ca8108b592144278c07891a0cbe8cdf8e15796efa`.
