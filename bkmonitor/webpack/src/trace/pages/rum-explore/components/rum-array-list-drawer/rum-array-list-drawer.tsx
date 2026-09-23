/*
 * Tencent is pleased to support the open source community by making
 * 蓝鲸智云PaaS平台 (BlueKing PaaS) available.
 *
 * Copyright (C) 2017-2025 Tencent.  All rights reserved.
 *
 * 蓝鲸智云PaaS平台 (BlueKing PaaS) is licensed under the MIT License.
 *
 * License for 蓝鲸智云PaaS平台 (BlueKing PaaS):
 *
 * ---------------------------------------------------
 * Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
 * documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
 * rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
 * permit persons to whom the Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all copies or substantial portions of
 * the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
 * THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
 * CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
 * IN THE SOFTWARE.
 */

import { type PropType, computed, defineComponent, onBeforeUnmount, shallowRef, useTemplateRef } from 'vue';

import { Exception, Switcher } from 'bkui-vue';
import { useI18n } from 'vue-i18n';

import { EVENTS_FIELD_PREFIX } from '../../constants';
import { ARRAY_ITEM_EMPTY_PLACEHOLDER, formatArrayItem, resolveArrayItemFormatter } from '../../utils';

import type { IRumField, IRumSpanRecord } from '../../typings';
import type { ArrayItemFormatter } from '../../utils';

import './rum-array-list-drawer.scss';

/** 抽屉默认高度（设计稿标注） */
const DEFAULT_DRAWER_HEIGHT = 440;
/** 抽屉最小高度 */
const MIN_DRAWER_HEIGHT = 200;
/** 拖拽调高时为上方表格预留的高度 */
const RESERVE_HEIGHT = 120;

/** 数组列表的一列：字段元数据 + 由字段语义推导出的格式化方法 */
interface IArrayListColumn {
  alias: string;
  formatter: ArrayItemFormatter;
  name: string;
}

/** 数组列表的一行：events 数组下标 + 各列在该下标的取值 */
interface IArrayListRow {
  index: number;
  values: string[];
}

/**
 * @description 把一条 span 的 events 数组按下标展开成列表行。
 *              各 events.* 字段的值按数组处理（非数组视为单项），行数取最长数组的长度，该下标缺值补空占位符。
 * @param {IRumSpanRecord | null} row 当前行数据
 * @param {IArrayListColumn[]} columns 参与展示的列
 * @returns {IArrayListRow[]} 列表行
 */
function buildArrayListRows(row: IRumSpanRecord | null, columns: IArrayListColumn[]): IArrayListRow[] {
  if (!row || !columns.length) return [];
  const lists = columns.map(column => {
    const value = row[column.name];
    if (value === null || value === undefined) return [];
    /** 非数组值按单项处理，与主表单元格口径一致 */
    return Array.isArray(value) ? value : [value];
  });
  const size = lists.reduce((max, list) => Math.max(max, list.length), 0);
  if (!size) return [];
  return Array.from({ length: size }, (_, index) => ({
    index,
    values: lists.map((list, columnIndex) =>
      index < list.length ? formatArrayItem(list[index], columns[columnIndex].formatter) : ARRAY_ITEM_EMPTY_PLACEHOLDER
    ),
  }));
}

export default defineComponent({
  name: 'RumArrayListDrawer',
  props: {
    /** 触发抽屉的行数据 */
    row: {
      type: Object as PropType<IRumSpanRecord | null>,
      default: null,
    },
    /** 当前选中的列键，抽屉中该列整列高亮 */
    colKey: {
      type: String,
      default: '',
    },
    /** 可作为列的字段全集，用于推导 events.* 数组字段 */
    fields: {
      type: Array as PropType<IRumField[]>,
      default: () => [],
    },
    /** 主表当前显示的字段（顺序即列顺序），默认态只展示其中的数组字段 */
    displayFieldKeys: {
      type: Array as PropType<string[]>,
      default: () => [],
    },
  },
  emits: {
    /** 点击右上角关闭 */
    close: () => true,
  },
  setup(props) {
    const { t } = useI18n();
    /** 抽屉根节点，拖拽调高时按父容器高度推算上限 */
    const drawerRef = useTemplateRef<HTMLElement>('drawerRef');
    /** 是否展示全部字段（含主表隐藏的数组字段） */
    const showAllFields = shallowRef(false);
    /** 抽屉高度 */
    const height = shallowRef(DEFAULT_DRAWER_HEIGHT);
    /** 拖拽调高的事件解绑函数，组件卸载时兜底清理 */
    let stopResize: (() => void) | null = null;

    /** 抽屉表格的列与行：列集合与「查看全部字段」开关联动，行随当前行数据转置 */
    const arrayList = computed<{ columns: IArrayListColumn[]; rows: IArrayListRow[] }>(() => {
      const eventFields = props.fields.filter(field => field.name.startsWith(EVENTS_FIELD_PREFIX));
      const displayKeys = props.displayFieldKeys ?? [];
      /** 默认态只展示主表已显示的数组字段，顺序沿用主表列顺序；开「查看全部字段」后展示全集 */
      const shownFields = showAllFields.value
        ? eventFields
        : eventFields
            .filter(field => displayKeys.includes(field.name))
            .sort((a, b) => displayKeys.indexOf(a.name) - displayKeys.indexOf(b.name));
      const columns: IArrayListColumn[] = shownFields.map(field => ({
        alias: field.alias,
        formatter: resolveArrayItemFormatter(field),
        name: field.name,
      }));
      return { columns, rows: buildArrayListRows(props.row, columns) };
    });

    /**
     * @description 拖拽调高：按下顶部拖拽条后跟随鼠标纵向位移改高度，上限为父容器高度减去预留高度
     * @param {MouseEvent} e 鼠标按下事件
     */
    function handleResizeStart(e: MouseEvent) {
      e.preventDefault();
      const startY = e.clientY;
      const startHeight = height.value;
      const maxHeight = Math.max(
        MIN_DRAWER_HEIGHT,
        (drawerRef.value?.parentElement?.clientHeight ?? 0) - RESERVE_HEIGHT
      );
      const handleMove = (event: MouseEvent) => {
        height.value = Math.min(maxHeight, Math.max(MIN_DRAWER_HEIGHT, startHeight + (startY - event.clientY)));
      };
      const handleUp = () => stopResize?.();
      stopResize = () => {
        document.removeEventListener('mousemove', handleMove);
        document.removeEventListener('mouseup', handleUp);
        stopResize = null;
      };
      document.addEventListener('mousemove', handleMove);
      document.addEventListener('mouseup', handleUp);
    }

    onBeforeUnmount(() => stopResize?.());

    return { arrayList, handleResizeStart, height, showAllFields, t };
  },
  render() {
    const { columns, rows } = this.arrayList;
    /** 副标题用于标识当前是哪一个 span：优先 span_name，缺失回落 span_id */
    const subtitle = String(this.row?.span_name || this.row?.span_id || '');
    return (
      <div
        ref='drawerRef'
        style={{ height: `${this.height}px` }}
        class='rum-array-list-drawer'
      >
        <div
          class='drawer-resize-handle'
          onMousedown={this.handleResizeStart}
        />
        <div class='drawer-header'>
          <div class='header-title-block'>
            <span class='drawer-title'>{this.t('查看数组列表')}</span>
            <span class='title-divider' />
            <span
              class='drawer-subtitle'
              v-overflow-tips={{ placement: 'top', theme: 'dark text-wrap' }}
            >
              {subtitle}
            </span>
          </div>
          <div class='header-actions'>
            <div class='show-all-field'>
              <Switcher
                size='small'
                theme='primary'
                value={this.showAllFields}
                onChange={(value: boolean) => (this.showAllFields = value)}
              />
              <span class='show-all-label'>{this.t('查看全部字段')}</span>
            </div>
            <i
              class='icon-monitor icon-mc-close drawer-close'
              onClick={() => this.$emit('close')}
            />
          </div>
        </div>
        <div class='drawer-body'>
          {rows.length ? (
            <table class='array-list-table'>
              <thead>
                <tr>
                  <th class='index-col'>#</th>
                  {columns.map(column => (
                    <th
                      key={column.name}
                      class={{ 'is-active': column.name === this.colKey }}
                    >
                      <span
                        class='col-text'
                        v-overflow-tips={{ placement: 'top', theme: 'dark text-wrap' }}
                      >
                        {column.alias}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map(item => (
                  <tr key={item.index}>
                    <td class='index-col'>{item.index}</td>
                    {item.values.map((value, index) => (
                      <td
                        key={columns[index].name}
                        class={{ 'is-active': columns[index].name === this.colKey }}
                      >
                        <span
                          class='cell-text'
                          v-overflow-tips={{ placement: 'top', theme: 'dark text-wrap' }}
                        >
                          {value}
                        </span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Exception
              class='drawer-empty'
              description={this.t('暂无数据')}
              scene='part'
              type='empty'
            />
          )}
        </div>
      </div>
    );
  },
});
