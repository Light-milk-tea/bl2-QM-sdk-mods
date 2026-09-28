# 无主之地 2 SDK 模组

个人制作的无主之地 2 模组，使用 Willow 2 Mod Manager 3.0 及以上的 SDK。每个模组只影响本机，联机时不会改其他玩家的游戏。

仓库：[https://github.com/Light-milk-tea/bl2-QM-sdk-mods](https://github.com/Light-milk-tea/bl2-QM-sdk-mods)

注：自制/汉化的mod是我个人调试，尚未请其他玩家测试，如有问题欢迎提issue

## 安装

游戏需要已经装好 [Willow 2 Mod Manager](https://github.com/bl-sdk/willow2-mod-manager) 3.0 或更新版本。

1. 进入对应模组文件夹，拿里面的 `.sdkmod`。
2. 复制到无主之地 2 的 `sdk_mods` 目录。Steam 一般是 `steamapps/common/Borderlands 2/sdk_mods`。
3. 完全退出游戏后再打开。
4. 在主菜单的 MODS 里启用。

游戏正在运行时不要覆盖已经加载的 `.sdkmod`。

## 模组


| 菜单名      | 版本    | 文件夹               | 作用                                               |
| -------- | ----- | ----------------- | ------------------------------------------------ |
| 移动速度修改   | 1.1.1 | `MoveSpeed_SDK`   | 改当前角色的地面移速，范围 0.0 到 5.0。1.0 是原速。                 |
| 输入法兼容修复  | 1.0.0 | `ImeFix_SDK`      | 游戏在前台时切到英语(美国)键盘，切出、关掉模组或退出游戏时换回原来的输入法。          |
| 自动拾取与开箱  | 1.0.0 | `AutoAssist_SDK`  | 走近时自动捡起弹药、钱、增益饮料和托格代币，并打开能搜的箱子、柜子。               |
| 跳过游戏启动动画 | 1.2.1 | `SkipStartup_SDK` | 启动时跳过 2K、Gearbox、版权页和存档图标页。DLC 检查和 SHiFT 登录仍会进行。 |


`汉化` 文件夹里是移植到新版 SDK 并汉化过的模组，`.sdkmod` 直接放在这个文件夹里。如果游戏里还留着旧目录 `sdk_mods\BSABT`、`sdk_mods\PythonPartNotifier`、`sdk_mods\Quickload`，先删掉再装。


| 菜单名      | 版本    | 文件                     | 作用                                                                                  |
| -------- | ----- | ---------------------- | ----------------------------------------------------------------------------------- |
| 出生点与快速旅行 | 1.2.0 | `better_travel.sdkmod` | 重新进入后在上次触发的重生站出生，并可随时打开快速旅行。默认 F2 打开快速旅行。原作者 Juso。                                  |
| 地图重载     | 1.1.0 | `map_reloader.sdkmod`  | 一键重载当前地图，用来反复刷取，也可以当作保存并退出。默认 F5 保存位置，F6 开关位置恢复，F7 重载不保存，F8 重载并保存。原作者 FromDarkHell。 |
| 装备部件提示   | 1.9.0 | `part_notifier.sdkmod` | 在物品卡上显示枪和装备用了哪些部件。原作者 apple1417。                                                    |
| 装备编辑     | 6     | `vendor_edit.sdkmod`   | 在游戏里用商店界面修改身上的装备。默认 F9 编辑，F10 复制代码，F11 粘贴代码。原作者 apple1417。                          |


