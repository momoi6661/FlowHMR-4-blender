# FlowHMR Windows 桌面版

适用于 64 位 Windows 10 / 11，见 [最低配置与支持设备](../README.md#最低配置与支持设备)。基础包带 Python 3.10 和界面依赖，可以直接打开软件。生成前请选择并启用推理环境：NVIDIA 使用 CUDA，Windows 11 上的 AMD Radeon 使用 [ROCm](amd-rocm.md)，兼容的 Intel Arc 可试用 Intel XPU；Intel 和 AMD x64 处理器也可选实验性的纯 CPU 推理。

1. 下载 [FlowHMR-Windows.zip](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)，完整解压到一个有写入权限的目录。
2. 在解包后的 `FlowHMR` 文件夹里双击 `FlowHMR.exe`。程序使用独立窗口，不需要用浏览器。缺少 WebView2 或 VC++ 运行库时，程序会运行包里的微软安装程序。
3. 右上角 **运行环境** 会按设备推荐：RTX 50 系列使用 CUDA 12.8；RTX 20／30／40 系列使用 CUDA 12.1。GTX 10 系列可用 CUDA 12.1 试跑短片，实际表现取决于显存和驱动。已装的 12.6／12.9 仍可保留。AMD 用户按 [ROCm 说明](amd-rocm.md)配置。软件内下载支持续传，安装会校验文件及实际算子。运行环境分别存入 `runtime/environments`，启用时重启推理服务。
4. 点击右上角 **模型** → **下载全部公开模型**，或逐行选择要下载的模型。登录 [MANO 官网下载页](https://mano.is.tue.mpg.de/download.php) 取得 **Models & Code**（`mano_v1_2.zip`，男／女体型及 MANO）或 **Extended SMPL+H model**（`smplh.tar.xz`，中性体型）。基础动捕有其中一种官方体型即可。点 **导入模型压缩包…** 直接选择下载的文件，程序会自动解压、识别并显示结果；也可选择已解压的文件夹。详见 [模型目录说明](../models/README.md)。开始生成时才会加载推理模型。

Windows 便携包自带 FBX 导出组件。安装运行环境和模型后，可离线生成、预览和导出 FBX、VMD、VRMA 或 NPZ。程序设置、日志和任务历史留在解包目录。可双击 `check-install.cmd` 检查环境和模型。

CUDA 12.1 环境使用 PyTorch 2.5.1。由于这个版本的 [权重读取安全问题](https://github.com/pytorch/pytorch/security/advisories/GHSA-53q9-r3pm-6pq6)，桌面版会校验 FlowHMR、SAM、VGGT、YOLOX 和 HaMeR 等公开推理权重的官方 SHA-256。

纯 CPU 推理在 **运行环境 → CPU** 安装（约 126 MB，模型另装）。固定机位与运动相机均能用 CPU 推理；没有原生 BF16 时 SAM 使用 FP32，耗时和内存占用会增加。HaMeR 手指精修目前需要 GPU，SAM 自带的手部估计仍会执行。建议先用短片试跑。

Windows 11 上的 Intel Arc A／B 系列和部分 Core Ultra Arc 核显可在 **运行环境 → Intel XPU** 试用。按 [Intel 的 PyTorch 驱动要求](https://www.intel.com/content/www/us/en/developer/articles/tool/pytorch-prerequisites-for-intel-gpu/2-12.html) 更新显卡驱动，下载完成后会检查本机 Intel GPU 上的算子。Intel GPU 负责 SAM 的图像编码；检测、相机估计、SAM 姿态解码、动作生成和导出由 CPU 完成。HaMeR 暂不支持此环境。普通 UHD 和旧 Iris Xe 不在这档的设备范围内。

便携版每次启动时在后台检查 GitHub，发现新版本会打开更新面板，显示版本号和发布说明。可以关闭面板继续工作，也可以从右上角语言下拉框旁的 **检查更新** 重新打开。下载由用户发起，校验完成后在下次启动时安装。更新源暂时无法访问时，仍可使用当前版本。模型、角色库、任务历史、设置和已安装的运行环境继续使用原来的目录。

打开 **检查更新** 面板，点 **手动更新** 可以直接选择已下载的官方 Windows ZIP，程序在后台校验和准备安装。**历史版本** 会列出 GitHub 发布的版本和本机缓存的上一个程序版本。本机缓存可离线切换。较早发布的 ZIP 可从列表下载并单独解压运行。

相机默认使用 **固定机位**。素材仅平移、推拉或缩放时通常可用此模式；镜头明显转向、俯仰或滚转时，选 **运动相机 · 自动**。独显优先保留正常分辨率和采样，显存不足时降低开销。AMD 核显推荐固定机位；选运动相机时，相机估计交给 CPU，自动最多采样 16 帧，系统内存不足时会尝试降低分辨率及采样密度。手动采样保留指定间隔，核显下过密时会提示加大间隔或改用自动。

人物检测只扫描所选起止帧，按区间内的实际人物轨迹生成动作；视频预览和多人对齐仍使用原素材帧号。重跑时改了起止帧，会重新检测新范围。单条轨迹超过 5000 帧时，任务面板会标出实验性长动作。

开启 **自动选择 SAM 批次** 后，软件先运行一帧，再根据实际峰值、显存预算和系统内存余量逐批调整。分配失败时会缩批并重试同一段素材。单帧也无法处理时，任务会提示需要释放哪种内存。可切换到手动批次指定帧数。SAM 手部采样可选逐帧、每 2 帧或每 4 帧。建议在开启 HaMeR 手指精修时使用隔帧采样。

HaMeR 手指精修使用独立的手部批次，不受 SAM 批次设置影响。8 GiB 及以下显卡从每批一只手开始，更大的显卡按可用显存选择批次。遇到 CUDA／HIP 显存不足时，自动缩小批次并重试同一批手部画面；其他推理错误保留原始原因。

导出 FBX 时可选 **不添加 / T Pose / A Pose**。参考姿势写在 FBX 第 **−1** 帧，原动作帧号和多人的入场时刻保持不变。在 Blender 导入时将 **Animation Offset** 设为 `0`，可直接看到文件里的负帧号。

## 从源码运行

下面的命令在项目根目录执行。源码版需要 Python 3.10 x64 和支持 CUDA 12.8 的 NVIDIA 驱动。先初始化依赖的 Git 子模块：

```powershell
git submodule update --init third_party/YOLOX third_party/sam-3d-body third_party/vggt-omega
```

### 使用源码环境（不打包 EXE）

源码版可以直接用仓库提供的配置脚本创建 Python 3.10 环境。脚本使用 `uv`，
Windows 接触修正使用与源码版本匹配的原生扩展。首次配置时，从对应的官方
Windows 发布包取得 `runtime/Lib/site-packages/motion_correction/_motion_correction.cp310-win_amd64.pyd`，
通过 `-CorrectionNative` 指定这个文件（将下面路径替换为实际路径）。安装器会校验固定的
SHA-256，将仓库内的 Python 源码、许可证和扩展打包到项目的 `.cache/correction/`。
运行时只使用项目 `.venv` 内安装的组件，后续可以移走提供扩展的发布包。
如果本机通过代理访问外网，可以传入代理地址：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File tools\windows\setup-source.ps1 `
  -CorrectionNative 'C:\path\to\_motion_correction.cp310-win_amd64.pyd' `
  -Proxy http://127.0.0.1:7890
```

脚本会创建 `.venv`，按仓库的 `uv.lock` 同步 Windows 推理依赖，
包括本地 editable YOLOX、RGBMocapCorrection 和经校验的 motion-correction wheel。
后续配置可省略 `-CorrectionNative`；用 `uv sync --locked` 会保留接触修正组件。
`.cache/correction/` 中的 wheel 不提交 Git，复制项目或清理缓存后需要重新准备。
修改依赖后用 `uv lock` 更新锁文件。
完成后不需要 `FlowHMR.exe`，可以直接启动后端：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File tools\windows\start-web.ps1 `
  -ServerOnly -Port 60550
```

Blender 桥接或其他客户端通过 `http://127.0.0.1:60550` 控制后端；模型文件仍需按
`models/README.md` 安装。

检查基础环境：`uv run --locked tools/windows/check_install.py --environment-only --json`。
这个检查会实际执行 CUDA 矩阵运算和 torchvision NMS；模型权重需要另行安装。
需要在桌面端导出 FBX 时，配置源码环境可加 `-InstallFbx`。脚本会校验下载文件，
把 FBX 导出组件安装到项目的 `.venv`，并验证能否导入。已有环境也可运行
`uv run --locked tools/windows/install_fbx.py` 补装。
检查接触修正：`uv run --locked python -c "from flowhmr.utils.runtime_capabilities import native_correction_available; assert native_correction_available(); print('Contact correction ready')"`。
在已有环境中补装时，可运行 `.\.venv\Scripts\python.exe tools/windows/install_correction.py --native 'C:\path\to\_motion_correction.cp310-win_amd64.pyd'`。
安装后重启后端并刷新网页，页面才会重新检测组件。
自行编译原生扩展需要 MSVC C++ 工具链；上述预编译安装路线无需编译器。
HaMeR 的推理代码和模型可在 Web 界面内下载。

Blender 插件位于 `blender_addon/`，安装和区间规则见 [插件说明](../blender_addon/README.md)。

直接从源码运行纯 CPU 或低显存分阶段推理前，请用 `python tools/windows/adapt_sam.py` 让 SAM 依照推理设备加载；便携包在打包时已应用。若之后需要从这份源码打包，请从保持原始子模块内容的干净副本构建。

## 打包

在已验证的 Python 3.10 x64 / cu128 环境下，准备微软签名的 [WebView2 x64 离线安装程序](https://developer.microsoft.com/microsoft-edge/webview2/#download-section) 和 [Visual C++ x64 可再发行组件](https://aka.ms/vs/17/release/vc_redist.x64.exe)，使用下列名称保存：`MicrosoftEdgeWebView2RuntimeInstallerX64.exe`、`vc_redist.x64.exe`。还需要固定版本的 HaMeR 和 DINOv3 推理代码。打包只带代码和环境，不带模型权重。

将 `tools/windows/install_fbx.py` 指定的 `fbxsdkpy` wheel 放入同一先决条件目录，文件名保持为 `fbxsdkpy-2020.1.post2-cp310-none-win_amd64.whl`。打包会校验 SHA-256，并只提取运行所需的 `fbx.pyd`。

将 Microsoft 的 [.NET Runtime 10.0.15 x64 ZIP](https://builds.dotnet.microsoft.com/dotnet/Runtime/10.0.15/dotnet-runtime-10.0.15-win-x64.zip) 和 [Windows Desktop Runtime 10.0.15 x64 ZIP](https://builds.dotnet.microsoft.com/dotnet/WindowsDesktop/10.0.15/windowsdesktop-runtime-10.0.15-win-x64.zip) 也放入该目录，分别保存为 `dotnet-runtime-win-x64.zip` 和 `windowsdesktop-runtime-win-x64.zip`。便携包从 `runtime/dotnet` 加载桌面运行环境，打包时校验两个文件的 SHA-512。

```powershell
git clone https://github.com/facebookresearch/dinov3.git .cache\dinov3
git -C .cache\dinov3 checkout 6876159a11b4df116f30f667f8c9888617df0751
curl.exe --fail --location https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/946fcce07b --output .cache\FFmpeg-9.0.2-Source.tar.gz
.\.venv\Scripts\python.exe -B tools\windows\package_release.py stage .cache\release-stage --prerequisites .cache\prerequisites --dinov3-source .cache\dinov3 --version X.Y.Z
.\.venv\Scripts\python.exe -B tools\windows\package_release.py pack .cache\release-stage dist\FlowHMR-Windows --ffmpeg-source .cache\FFmpeg-9.0.2-Source.tar.gz
```

暂存和输出目录必须尚不存在。打包会生成 `FlowHMR-Windows.zip`、校验文件、源码和相对当前 Git 提交的补丁。需要制作预装 cu128 的离线包时，可给 `stage` 加 `--bundle-cuda`；超出单附件大小的离线包才会分卷。

将 `X.Y.Z` 换成即将发布的版本号，发布标签使用 `windows-vX.Y.Z`。将生成的 `FlowHMR-Windows.zip` 和 `FlowHMR-Windows-Update.json` 一起作为发布附件上传，历史版本列表会据此显示应用内安装入口。下载校验使用 GitHub 提供的 SHA-256。首次从未含更新器的版本升级，以及启动器本身升级时，请完整解压发布包。


## 手部和脚部姿势预设

生成结果加载完成后，点击 3D 视图顶部的“手脚预设”。选择人物，在时间轴暂停到合适帧，读取当前帧并命名保存。支持左手、右手、双手、左脚、右脚和双脚。手部包含手腕及手指；脚部只包含脚踝、脚趾，不包含膝盖、髋部或人物位移。保存的是生成动作的 SMPL-H 局部旋转，不包含预览角色的骨骼调节。

预设保存在项目 `output/pose_presets/*.json`，不进入 Git，刷新页面或重启后仍保留，也不随清空任务历史删除。支持 JSON 导入、导出及删除。应用时选择动作帧范围（从所选人物动作第 1 帧开始，包含结束帧）、强度和边缘过渡帧数，也可以选择仅当前帧或整段动作。

应用预设生成独立的历史任务，保留原结果和原视频。范围外的姿势、其他关节及人物位移保持原值；范围内按最短旋转路径混合，边缘过渡发生在所选区间内。更改脚部会移除失效的原脚部接触标签。该功能保存局部姿势，不创建门、把手、IK 接触约束或自动锁脚。副本可使用现有 NPZ、FBX、VMD、VRMA 导出流程，也可由 Blender 桥接导入。

网格重建使用 CPU 上的 SMPL-H，不加载 FlowHMR / SAM / HaMeR 推理模型；应用预设本身不占用 CUDA 显存。多人结果可以选择单个人物保存或应用；应用后得到这个人物的独立动作副本，不自动替换原多人场景。
