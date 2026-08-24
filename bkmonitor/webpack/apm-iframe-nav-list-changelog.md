# APM iframe 嵌入支持导航栏显隐控制与服务参数联动

为提升 APM 页面以 iframe 方式嵌入外部系统时的灵活性，iframe 链接新增以下两个参数：

| 参数 | 作用 | 取值 |
|------|------|------|
| `apm_nav_list` | 控制是否展示 APM 导航栏面包屑列表 | `true` 显示，`false` 隐藏 |
| `filter-service_name` | 服务参数联动，指定初始服务 | 服务名称 |

## 链接示例

```text
https://xxx/?parentOrigin=https://your-host&needMenu=false&apm_nav_list=false#/apm/application?filter-app_name=xxx&filter-service_name=xxx
```

## 说明

- `apm_nav_list=false` 时隐藏 APM 导航栏面包屑列表，适用于外部系统已自带导航、需避免重复的场景。
- `filter-service_name` 与服务参数联动能力配合，可指定初始选中的服务（需结合 `filter-app_name` 使用），实现应用/服务级跳转联动。
- **防重复跳转报错**：父页面（外部系统）经 iframe 桥接重复下发相同参数（如应用/服务名一致）时，先前会直接执行 `$router.replace`/`$router.push` 触发 vue-router 的 `NavigationDuplicated` 报错。现于 `Application` / `Service` 页面的 `handleExternalParams` 中，先通过 `this.$router.resolve(...)` 解析出目标路由，仅当 `targetRoute.resolved.fullPath !== this.$route.fullPath`（即目标路由与当前路由不一致）时才执行跳转，避免无意义的重复跳转与报错。

## 精简版（变更摘要）

> APM iframe 链接新增 `apm_nav_list`（控制 APM 导航栏面包屑列表显隐）与 `filter-service_name`（服务参数联动）两个参数，便于外部系统按需嵌入与参数联动；并通过 `router.resolve` 预校验目标路由，避免父页面重复下发相同参数时触发重复跳转报错。
