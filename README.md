# 途强转 Traccar

Home Assistant 自定义集成：定时读取途强平台的设备定位，并通过 Traccar 的 OsmAnd 协议转发。

## 安装

在 HACS 中打开“集成” → 右上角菜单 → “自定义存储库”，添加：

https://github.com/jimgo188/tuqiang_traccar

类别选择“集成”，安装后重启 Home Assistant。

也可以下载 [Releases](https://github.com/jimgo188/tuqiang_traccar/releases) 中的版本，将 `custom_components/tuqiang_traccar` 复制到 Home Assistant 配置目录下的 `custom_components` 目录。

## 配置

在 Home Assistant 中进入“设置” → “设备与服务” → “添加集成”，搜索“途强转Traccar”，按向导输入途强账号、设备和 Traccar 客户端接入地址。

Traccar OsmAnd 接入地址通常使用 5055 端口，而不是网页管理端口 8082。

## 致谢

本项目基于 [lambilly/ha_tuqiang_traccar](https://github.com/lambilly/ha_tuqiang_traccar) 修改。
