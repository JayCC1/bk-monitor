# RUM 检索表格 · events.\* 数组字段单元格渲染设计

- 日期：2026-09-23
- 范围：`src/trace/pages/rum-explore`（Vue3 / TSX）
- 设计稿：Figma「🔍 数据探索」`node-id=986-26329`（单元格）、`node-id=2587-19006`（抽屉，本期不做）

---

## 1. 背景与目标

RUM 列表数据中 `events.*` 下的属性可能是数组。当前实现把所有结构化值统一 `JSON.stringify` 后按纯文本展示，单元格里出现 `[1.3,2.3,3.2,1.3,0.8,2.1,1.5]` 这类内容，可读性差。

目标：为数组值提供专用的内联渲染样式 —— `值 , 值 , 值 , 值 +N`，超出列宽的部分折叠为 `+N`，hover 展示剩余值。

## 2. 范围

### 本期做

- `events.*` 字段数组值的单元格内联渲染
- `events.attributes.exception.type` 数组值适配（多个红色 Tag）
- `events.timestamp` 数组值适配（逐项格式化时间后套数组样式）

### 本期明确不做

- 设计稿中的底部「查看数组列表」抽屉（`2587:19006`）：含按下标 `#` 对齐的多字段对照表格、当前列高亮、可拖拽调高、「查看全部字段」开关。留待下期
- 非 `events.*` 列的数组/对象值：保持现有 `JSON.stringify` 行为
- 数组值的「加为检索条件」菜单：`events.*` 列不提供，后续该类列点击后另有交互

## 3. 设计稿规格

从 `get_metadata` 逐字坐标与 `get_variable_defs` 提取（`get_design_context` 请求超时，未取到生成代码，规格以坐标与变量为准）：

| 元素 | 规格 | Figma 变量 |
| --- | --- | --- |
| 值文本 | `#4d4f56`，12px / line-height 20 | `Text/2 标准` |
| 分隔符 `,` | `#F239C1` | **`Array`** |
| `+N` | `#F239C1`，纯文本、无背景无 padding | **`Array`** |
| 项间距 | 4px（值↔逗号、逗号↔值、末值↔`+N` 均为 4px） | — |

逐字坐标佐证（157px 宽列）：`1.3(x=0,w=17)` `,(x=21,w=4)` `2.3(x=29)` `,(x=50)` `3.2(x=58)` `,(x=79)` `1.3(x=87,w=17)` `+3(x=108,w=21)`。

**末值之后没有逗号** —— 故分隔符渲染在「非首项之前」，而非「非末项之后」，以保证任意折叠位置都不残留尾逗号。

单元格另带「光标-抓手」标注（可点击），但本期点击交互属于抽屉范畴，不实现。

## 4. 技术调研结论

| 项 | 结论 |
| --- | --- |
| 数组判定 | ⚠️ 后端字段元数据**无数组标识**。`DimensionType` 仅 `boolean/date/double/float/integer/keyword/long/object/text`，`IRumField`/`IRumRawField` 无 `is_array`。只能运行时 `Array.isArray` |
| 现状代码 | `base-scenario.tsx:96` `if (typeof value === 'object') return JSON.stringify(value)` |
| 溢出测量 | `collapse-tags.tsx`（组件名 `TagShow`）的测量基于 `sectionRef.children` 的真实 `getBoundingClientRect`，**与 `Tag` 组件无耦合**，`customTag` slot 可整体接管每项渲染 → 可直接复用 |
| 间距吻合 | `CollapseTags` 默认 `tagColGap = 4`，与设计稿 4px 完全一致，用默认值即可 |
| 条件菜单 | 由 `.${ENABLED_TABLE_CONDITION_MENU_CLASS_NAME}` 类名事件委托触发 → 不挂类名即自然禁用，无需改 `use-cell-condition-menu.ts` |
| `RUM_TIME_FIELDS` | `constants.ts:147` 定义后**全 `src/trace` 零消费点（死代码）**，不会与 `buildBaseline` 的 DATETIME 分支冲突。本次不删除 |

## 5. 架构设计

### 5.1 核心问题

「是否数组」只能**按行**在运行时判断，而 `resolveColumnConfig` 是**列级**、每列只执行一次。因此不能按列切换 `renderType`，只能对 `events.*` 列声明 `cellRenderer`，在其内部按行分流。

### 5.2 方案：baseline 格式化语义抽成 itemFormatter

```
buildBaseline(colKey):
  baseline = <现有推导：DATETIME→TIME / DURATION→DURATION / field_unit→formatUnitValue>

  if (!colKey.startsWith(EVENTS_FIELD_PREFIX)) return baseline   // 非 events.* 完全不变

  return {
    ...baseline,
    renderType: undefined,                      // cellRenderer 与 renderType 互斥
    cellRenderer: (row, column, ctx) => {
      const raw = row[colKey]
      if (!Array.isArray(raw))                  // 单值 → 回落原渲染类型
        return ctx.cellRenderHandleMap[baseline.renderType ?? TEXT]?.(row, column, ctx)
      return <ArrayCell values={raw.map(itemFormatter)} />
    },
  }
```

`itemFormatter` 由 baseline 语义决定：

| baseline 语义 | itemFormatter |
| --- | --- |
| `field_display_type === DATETIME` | 时间格式化 |
| `field_display_type === DURATION` | `formatDuration(v, '', 2, field_unit)` |
| 有 `field_unit` | `formatUnitValue(v, unit)` |
| 其余 | `String(v)` |

**收益**：`events.timestamp` 数组自动「先格式化时间再套数组样式」（符合需求），带单位的数值数组（如 `script.duration` 为 ms）自动带单位，未来新增 `events.*` 字段无需逐列加代码。

### 5.3 不改动的文件（重要）

- `base-scenario.tsx` —— `JSON.stringify` 兜底保留，只作用于非 `events.*` 列
- `use-cell-condition-menu.ts` —— 条件菜单由类名触发，不挂即禁用

## 6. ArrayCell 组件

新增 `components/array-cell/array-cell.tsx`（约 40 行），职责只有三项：空态、逐项渲染、样式覆盖。

```tsx
<CollapseTags
  class='explore-col rum-array-col'
  data={values}                               // 已被 itemFormatter 格式化过的字符串数组
  tagColGap={4}
  ellipsisTip={list => <换行列表>}             // 剩余值每行一个
  v-slots={{ customTag: (v, i) => (
    <span class='array-value-item'>
      {i > 0 && <span class='array-value-sep'>,</span>}
      <span class='array-value-text'>{v}</span>
    </span>
  )}}
/>
```

### 样式（`array-cell.scss`）

```
--rum-array-sep-color: #F239C1;   // 抽变量，待设计确认后可一行切换
```

- `.array-value-text`：`#4d4f56`，12px / 20px
- `.array-value-sep`、`.collapse-tag`：`var(--rum-array-sep-color)`
- 覆盖 `collapse-tags.scss` 中 `.collapse-tag` 的灰底 `#f0f1f5`、`padding: 0 5px`、`border-radius: 2px`
- `.item-tags` 覆盖 `flex-wrap: nowrap`（数组为单行语义）

> ⚠️ 覆盖 `.collapse-tag` 会**同时**作用于隐藏测量层 `.collapse-tag-fill`（同类名）。这是必需的 —— 否则测量宽度多算 10px padding，会导致少显一项。

### 两个必须遵守的约束

1. **tooltip 内容用格式化后的值**。否则单元格显示 `2.5s`、tooltip 显示 `2500`，前后不一致
2. **不挂 `isEnabledCellEllipsis`**。否则表格的「完整文本溢出 tip」会与 `+N` 的 tippy 同时弹出（`span-scenario.tsx:196-199` 已有同类踩坑注释）

## 7. 特化列处理

### `events.attributes.exception.type`

在 `columnOverrides` 中声明为 TAGS，优先级高于 `buildBaseline`，故不走 §5.2 分流，单独适配：

```ts
private getExceptionTypeRenderValue(value: unknown) {
  const list = Array.isArray(value) ? value : [value];
  return list.filter(v => v != null && v !== '')
             .map(v => ({ alias: String(v), tagBgColor: '#FDE7E7', tagColor: '#EA3636', … }));
}
```

TAGS 列本身走 `TagsCell → CollapseTags`，**多个红色 Tag + `+N` 折叠是免费得到的**，无需新组件。

该列的 tag 自带 `data-index` 与条件菜单类名，而 `getCellComplexValue(…, { index })` 按下标取值本来就是正确行为 —— 条件值正确，**保留不动**（与其余 `events.*` 列的"无菜单"存在行为差异，已确认接受）。

### `events.timestamp`

走 §5.2 的 DATETIME 分支 `itemFormatter`。

## 8. 边界与异常

| 情况 | 处理 |
| --- | --- |
| 空数组 `[]` | `CollapseTags` 在 `dataLen === 0` 时 `return undefined`（整格空白）。故 `ArrayCell` 前置判断，渲染统一空占位符 |
| 元素为 `null` / `undefined` | `itemFormatter` 兜底为空占位符 |
| 元素为 object | `itemFormatter` 兜底 `JSON.stringify` |
| 单个值即超宽 | `visibleCount = 0`，退化为只显示 `+N`。设计稿未定义此态，**沿用 `CollapseTags` 既有行为**，不特殊处理 |
| 非数组单值 | 回落原 `renderType`，行为与改动前完全一致 |

## 9. 影响面

- **受影响**：`colKey.startsWith('events.')` 的所有列；`events.attributes.exception.type` 列
- **不受影响**：所有非 `events.*` 列的取值与渲染、条件菜单逻辑、其他场景（session / error 等）的基类行为、trace 检索表格

## 10. 风险与待确认

| 项 | 等级 | 说明 |
| --- | --- | --- |
| 无法确认数组真实形态 | 🔴 高 | `rum-explore` 目录 103 个文件无任何 mock/fixture，全走 `monitor-api/modules/rum_query`。元素是 number 还是 object、是否嵌套、最大长度均未知。**建议与后端对齐** |
| `#F239C1` 是否为实际 UI 色 | 🟡 中 | Figma 变量名为 `Array`，疑似「数据类型标注色」。已抽成 scss 变量，**需与设计确认** |
| 后端缺数组元数据 | 🟡 中 | 只能运行时判定，表头无法预先标识数组列。**建议推动后端在字段元数据中补数组标识** |
| 首屏折叠闪动 | 🟢 低 | `CollapseTags` 200ms 防抖 + rAF，既有 tag 列同样表现 |

## 11. 文件清单

| 文件 | 动作 |
| --- | --- |
| `…/rum-explore/constants.ts` | 新增 `EVENTS_FIELD_PREFIX = 'events.'` |
| `…/rum-explore-table/scenarios/span-scenario.tsx` | 原 `buildBaseline` 主体改名 `buildFieldBaseline`；新 `buildBaseline` 做 events.\* 分流；新增 `withArrayCellRenderer` / `resolveArrayItemFormatter` / `formatArrayItem`；改 `getExceptionTypeRenderValue` 兼容数组 |
| `…/rum-explore-table/components/array-cell/array-cell.tsx` | **新增** |
| `…/rum-explore-table/components/array-cell/array-cell.scss` | **新增**（由 `array-cell.tsx` 自行 `import`，与 `tags-cell.tsx` 一致，故**无需**改 `rum-explore-table.scss`） |
| `…/rum-explore-table/scenarios/base-scenario.tsx` | 不改 |
| `…/rum-explore-table/hooks/use-cell-condition-menu.ts` | 不改 |

## 12. 验证计划

1. 在 `span-scenario` 中临时注入数组 mock 值，自测视觉与折叠行为，**验完删除，不进提交**
2. 覆盖用例：纯数字数组、长字符串数组、单元素数组、空数组、含 null 元素、超长单值、列宽拖拽前后重算、`exception.type` 多值、`events.timestamp` 多值
3. 对照 Figma 截图做视觉自检（间距 4px、逗号位置、末值无尾逗号、`+N` 无背景）
4. 静态检查：lint + 类型
