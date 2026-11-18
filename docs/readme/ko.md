# FlowHMR-Windows

[简体中文](../../README.md) · [繁體中文](zh-TW.md) · [English](en.md) · [日本語](ja.md) · **한국어** · [Русский](ru.md) · [Français](fr.md)

**FlowHMR-Windows**는 내 컴퓨터에서 동영상으로 모션을 캡처하는 앱입니다. 동영상을 가져와 한 명 이상의 인물을 선택하면 3D 동작을 재구성해 원본과 동기화된 미리보기로 확인하고 내보낼 수 있습니다. 동작 재구성에는 업스트림 [FlowHMR](https://github.com/flowhmr/flowhmr) 모델을 사용합니다. 이 저장소는 Windows 포터블 버전, 모델과 런타임 관리, 인물 선택부터 내보내기까지의 데스크톱 작업 흐름을 독립적으로 유지합니다.

**[Windows 포터블 버전 다운로드](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)** · [릴리스 노트](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest) · [FlowHMR 논문](https://arxiv.org/abs/2610.03691)

## v0.3.6 변경 사항

- **사용자 지정 리타게팅과 프리셋.** 위치와 크기를 바꿀 수 있는 패널에서 3D 본을 선택합니다. 안내는 몸에서 양손까지 이어지며 자동 미러가 기본으로 켜집니다. 대상의 키와 팔다리 길이에 맞춰 동작을 조정하고 캐릭터와 프리셋을 저장할 수 있습니다. 둘 다 썸네일로 찾아볼 수 있습니다.
- **현재 캐릭터로 내보내기.** Mixamo와 MetaHuman에서 내보낼 때 템플릿 파일을 다시 고를 필요가 없습니다. 저장한 리타게팅과 보정을 적용하며, Mixamo에서는 범용 골격도 선택할 수 있습니다. 호환 PMX는 IK와 FK를 지원합니다. 루트 자동 매핑은 `センター`를 먼저 찾고, 쓸 수 없으면 `全ての親`를 사용합니다. 사용자 지정 FBX를 리타게팅한 뒤에는 현재 캐릭터 FBX를 선택해 원본 골격을 내보내고 스킨 포함 여부를 정할 수 있습니다. 현재 캐릭터 FBX는 한 번에 한 명씩 내보내며, 다인 장면에서도 위치와 등장 시점을 유지합니다. VRM은 GLB와 VRMA 내보내기를 계속 지원합니다.
- **범용 VMD에도 캐릭터 보정 적용.** 캐릭터를 적용하면 표준 MMD 본 이름을 유지하면서 관절 회전과 이동, 루트 보정, 발 IK/FK를 반영합니다. 캐릭터 고유의 비율과 발바닥 접지를 유지하려면 현재 PMX 캐릭터로 내보내세요.
- **더 부드러운 느린 재생.** 타임라인 표시 프레임 속도를 변경하고 저장할 수 있습니다. 느리게 재생하면 중간 자세를 보간하며 원본 동영상과의 동기화와 내보내기 시간은 그대로 유지됩니다.
- **AMD Radeon RX 6000(실험 중).** Windows 11에서 GPU 아키텍처에 맞는 ROCm 런타임을 선택할 수 있습니다. 패키지는 확인했으며 RX 6000 실기에서 전체 모션 캡처 과정은 아직 검증하지 않았습니다.
- **MCP 호환성.** 기존 도구 19개와 백그라운드 내보내기 옵션은 그대로 호환됩니다. 컨트롤러를 지정한 사용자 지정 FBX·VRM의 IK를 전환하고 현재 장치 백엔드를 확인할 수 있습니다. 캐릭터 보정을 굽는 내보내기는 데스크톱에서 수행합니다.

[전체 릴리스 노트](../release-windows.md) · [이전 릴리스 v0.3.5](../release-windows-v0.3.5.md)

## 시스템 요구 사항 및 지원 장치

| 항목 | 기준 사양 | 권장 사양 |
| --- | --- | --- |
| 운영체제 | 64비트 Windows 10 / 11 | 64비트 Windows 11(ROCm 및 Intel XPU에 필요) |
| 시스템 메모리 | 최소 16 GB | 32 GB 이상. CPU 전용 또는 내장 GPU에 특히 권장하며 긴 동영상에는 더 필요할 수 있습니다. |
| CPU 전용 모드 | Intel / AMD x64. GPU나 VRAM 불필요 | 시스템 메모리 32 GB 이상 |
| VRAM(외장 GPU에만 해당) | 6 GB에서 동작 확인 | 8 GB 이상 |

| 장치 | 런타임 | 검증 상태 |
| --- | --- | --- |
| NVIDIA GeForce RTX 50 | CUDA 12.8 | 검증 완료 |
| NVIDIA GeForce RTX 30 / 40 | CUDA 12.1 | 검증 완료 |
| NVIDIA GeForce RTX 20 | CUDA 12.1 | 실험 중 |
| NVIDIA GeForce GTX 10 | CUDA 12.1 | 실험 중 |
| AMD Radeon RX 9000 / 7000 | [ROCm](../amd-rocm.md) | 검증 완료 |
| AMD Radeon RX 6000 | [ROCm](../amd-rocm.md) | 실험 중 |
| AMD Radeon RX 5000 | [ROCm](../amd-rocm.md) | 실험 중 |
| AMD Radeon 내장 GPU(740M / 760M / 780M 및 일부 800M 포함) | [ROCm](../amd-rocm.md) | 실험 중(9950X 내장 GPU 검증 완료, 고정 카메라 권장) |
| Intel Arc A / B 및 일부 Core Ultra 내장 Arc GPU | [Intel XPU](../windows.md) | 실험 중(GPU는 SAM 이미지 인코딩만 담당) |
| Intel / AMD x64 CPU | [CPU 전용](../windows.md) | 검증 완료(AMD Ryzen 9 9950X), Intel은 실험 중 |

CPU 전용 모드와 Intel XPU는 아직 HaMeR 손가락 보정을 지원하지 않습니다. SAM의 기본 손 자세 추정은 그대로 실행됩니다.

## Windows 데스크톱 버전

- **한 창에서 모션 캡처.** 처리할 인물과 프레임 범위를 선택한 뒤 원본 동영상과 3D 모션을 나란히 확인할 수 있습니다. 재생, 탐색, 반복은 타임라인에서 동기화됩니다. 원본 오디오를 재생하고 스킨 메시와 스켈레톤 표시를 전환할 수 있습니다.
- **여러 인물을 함께 미리 보고 내보내기.** 같은 동영상에서 여러 명을 선택할 수 있습니다. 통합 미리보기와 FBX에는 원본 영상에서의 상대 위치와 각 인물의 등장 시점이 유지됩니다. 이전 작업은 썸네일이 있는 기록에서 다시 열 수 있습니다.
- **영상에 맞춰 모션 조정.** 모션 스무딩, 발 접촉 보정, 바닥 높이를 조절할 수 있습니다. 더 자세한 손가락 모션이 필요하면 HaMeR를 켜세요. 기본값은 고정 카메라입니다. 카메라가 뚜렷하게 방향을 바꾸거나 위아래로 기울거나 회전할 때 이동 카메라를 선택하세요.
- **로컬에서 실행.** 필요한 추론 환경과 모델을 설치하면 생성이 시작될 때만 모델을 불러옵니다. 설정이 끝나면 오프라인에서도 처리할 수 있습니다. 简体中文, 繁體中文, English, 日本語, 한국어, Русский, Français를 지원하며 앱을 다시 열면 마지막으로 선택한 언어를 사용합니다.
- **동작 내보내기.** FlowHMR, Mixamo, MetaHuman 및 Maya AdvancedSkeleton용 FBX, MMD VMD, VRM Animation(VRMA), NPZ를 지원합니다. 사용자 지정 FBX 내보내기는 캐릭터의 원본 골격과 저장된 동작 보정을 유지하며 스킨 포함 여부를 선택할 수 있습니다. 사용자 지정 매핑을 적용한 VRM은 스킨과 애니메이션을 포함한 GLB로도 내보낼 수 있습니다. FlowHMR과 범용 Mixamo FBX는 스킨과 **−1 프레임**의 T 포즈 또는 A 포즈를 선택할 수 있습니다. [내보내기 및 리타게팅 안내](../retarget-export.md)
- **사용자 지정 캐릭터.** PMX, VRM 0.x 또는 1.0, 스킨이 있는 Mixamo·MetaHuman FBX를 지원하며 인물마다 다른 캐릭터를 지정할 수 있습니다. 본을 매핑하고 IK 컨트롤러, 회전 및 이동을 조정하거나 저장한 프리셋을 불러와 관리할 수 있습니다.

## 예제 동영상

썸네일을 클릭하면 MP4가 열립니다. 네 영상 모두 중립 SMPL-H 체형을 30 FPS의 정면 직교 뷰로 표시하며 선택한 범위의 원본 오디오와 동기화되어 있습니다.

| 드리블과 댄스 스텝 | 춤 |
| --- | --- |
| [![드리블과 댄스 스텝](../../assets/demos/01-ikun-thumb.png)](../../assets/demos/01-ikun-front.mp4) | [![춤](../../assets/demos/02-selei-thumb.png)](../../assets/demos/02-selei-front.mp4) |
| **무대 캐릭터의 움직임** | **손동작과 스텝** |
| [![무대 캐릭터의 움직임](../../assets/demos/03-test2-thumb.png)](../../assets/demos/03-test2-front.mp4) | [![손동작과 스텝](../../assets/demos/04-test3-thumb.png)](../../assets/demos/04-test3-front.mp4) |

직접 실행해 보려면 저장소의 [예제 영상](../../assets/example/example.mp4)을 사용하세요.

## 빠른 시작

1. 64비트 Windows 10 또는 11에서 `FlowHMR-Windows.zip` 전체를 압축 해제합니다. 압축을 푼 `FlowHMR` 폴더를 열고 `FlowHMR.exe`를 실행합니다. 기본 패키지에는 Python, 데스크톱 UI 및 FBX 내보내기 구성 요소가 포함됩니다.
2. 오른쪽 위의 **런타임**을 열고 위 표를 참고해 장치에 맞는 환경을 다운로드하고 활성화합니다. GPU를 사용하려면 호환 드라이버를 설치하세요. **CPU**를 선택할 경우 별도 GPU 드라이버는 필요하지 않습니다.
3. **모델**을 열어 **공개 모델 모두 다운로드**(下载全部公开模型)를 선택하거나 하나씩 다운로드합니다. 공개 모델은 검증 후 `models/`에 배치되며 SAM 3D Body와 VGGT-Omega는 ModelScope에서 받습니다. 손가락 보정이 필요 없다면 개별 다운로드에서 HaMeR를 건너뛸 수 있습니다. 로그인이 필요한 공식 모델은 아래 표를 참고하세요.
4. 앱에 동영상을 끌어 놓고 인물과 프레임 범위를 선택해 생성을 시작합니다. 생성된 모션을 미리 본 뒤 메뉴에서 FBX, VMD, VRMA 또는 NPZ로 내보낼 수 있습니다.

앱은 백그라운드에서 업데이트를 확인합니다. 언어 메뉴 옆의 **업데이트 확인**에서 변경 사항을 읽거나, 업데이트 ZIP을 직접 선택하거나, 이전 버전을 선택할 수 있습니다. GitHub에 연결할 수 없어도 현재 버전을 계속 사용할 수 있습니다.

### 어떤 공식 모델을 받아야 하나요?

[MANO 공식 다운로드 페이지](https://mano.is.tue.mpg.de/download.php)에 로그인하세요. **기본 모션 캡처는 앞 단계에서 설치한 공개 모델에 아래 공식 패키지 중 하나만 추가하면 됩니다. `mano_v1_2.zip`이 이미 있다면 중립 체형 패키지를 따로 받지 않아도 됩니다.** 두 패키지를 모두 설치하면 기본적으로 중립 체형이 사용됩니다.

| 용도 | 공식 페이지의 다운로드 항목 | 수동 압축 해제 위치 |
| --- | --- | --- |
| 기본 모션 캡처에 사용할 수 있는 중립 체형 | **Extended SMPL+H model**(AMASS), `smplh.tar.xz` | `models/body_models/smplh/` |
| 기본 모션 캡처에 사용할 수 있는 남성·여성 체형과 손가락 보정에 필요한 MANO 파일 | **Models & Code**, `mano_v1_2.zip` | `models/` |

다운로드 후 **모델 → 모델 압축 파일 가져오기…**(模型 → 导入模型压缩包…)에서 `mano_v1_2.zip` 또는 `smplh.tar.xz`를 직접 선택하세요. 앱이 자동으로 압축을 풀고 모델을 확인해 알맞은 위치에 설치합니다. 원본 압축 파일은 그대로 유지되며, 모델 창에서 준비된 그룹과 누락된 파일을 확인할 수 있습니다. 이미 압축을 풀었다면 **압축을 푼 폴더 가져오기…**(导入已解压文件夹…)를 사용하세요.

손가락 보정에는 `mano_v1_2.zip`의 MANO 파일과 공개 **HaMeR** 모델이 필요합니다. HaMeR는 해당 행에서 개별 다운로드하거나 **공개 모델 모두 다운로드**로 함께 받을 수 있습니다.

수동 설치의 폴더 구조, 다운로드 출처, 각 모델의 용도는 [모델 파일 안내](../../models/README.md)를 참고하세요.

## 도움말 및 프로젝트 출처

- [Windows 사용, 업그레이드 및 소스 빌드](../../docs/windows.md) · [문제 신고](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/issues/new)
- [UI 번역 유지 관리](../../docs/localization.md)
- [FlowHMR 공식 프로젝트 페이지](https://flowhmr.github.io/) · [업스트림 추론 및 학습 문서](https://github.com/flowhmr/flowhmr#readme) · [업스트림 인용 형식](https://github.com/flowhmr/flowhmr#citation)

모션 재구성은 Zhanke Wang 등의 **FlowHMR: Physically Plausible Motion Capture from Video**를 기반으로 합니다. 연구에 사용하는 경우 [원본 논문](https://arxiv.org/abs/2610.03691)을 인용해 주세요. FlowHMR 소스 코드는 [Apache-2.0](../../LICENSE)을 따릅니다. 타사 구성 요소와 모델은 각자의 라이선스에 따라 배포됩니다. 릴리스 패키지의 `THIRD_PARTY_NOTICES.md`와 [업스트림 모델 카드](https://huggingface.co/fafsaf1/flowhmr-0.46B)를 참고하세요.
