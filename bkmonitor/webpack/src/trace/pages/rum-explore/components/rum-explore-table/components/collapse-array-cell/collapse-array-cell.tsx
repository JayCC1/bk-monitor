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
 * CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

import { type PropType, defineComponent } from 'vue';

import CollapseTags from '../../../../../trace-explore/components/trace-explore-table/components/table-cell/collapse-tags';

import type { SlotReturnValue } from 'tdesign-vue-next';

import './collapse-array-cell.scss';

/**
 * @description 折叠数组单元格：按「值 , 值 , 值 +N」的形式内联展示数组，超出列宽的部分折叠为 +N，
 *              hover +N 以换行列表展示剩余值。
 *
 * 复用 CollapseTags 的溢出测量能力（其测量基于子元素真实 getBoundingClientRect，与 Tag 组件无耦合），
 * 通过 customTag 插槽把每一项替换为纯文本，故列宽拖拽后可见数量会自动重算。
 *
 * 不挂条件菜单类名（events.* 数组列暂不提供「加为检索条件」），
 * 也不挂单元格溢出省略类名（否则表格的完整文本 tip 会与 +N 的 tippy 同时弹出）。
 */
export default defineComponent({
  name: 'CollapseArrayCell',
  props: {
    /** 已由调用方按字段语义格式化过的数组项文本（如耗时已带单位、时间已格式化） */
    values: {
      type: Array as PropType<string[]>,
      default: () => [],
    },
  },
  setup() {
    /**
     * @description 溢出项提示内容：每行一个值，数组项较长时比逗号拼接更易读
     * @param {string[]} ellipsisList 被折叠的数组项
     * @returns {SlotReturnValue} popover 展示的内容
     */
    const ellipsisTipRender = (ellipsisList: string[]) =>
      (
        <div class='ellipsis-tip-content'>
          {ellipsisList.map((value, index) => (
            <div
              key={`${index}_${value}`}
              class='ellipsis-tip-item'
            >
              <div class='item-prefix'>
                <i class='item-hyphen' />
              </div>
              <div class='item-text'>{value}</div>
            </div>
          ))}
        </div>
      ) as unknown as SlotReturnValue;

    return { ellipsisTipRender };
  },
  render() {
    return (
      <CollapseTags
        class='explore-col rum-collapse-array-col'
        v-slots={{
          /**
           * 分隔符渲染在「非首项之前」而非「非末项之后」：
           * 折叠发生在任意位置时，末尾都不会残留孤立逗号。
           */
          customTag: (value: string, index: number) => (
            <span class='collapse-array-value-item'>
              {index > 0 && <span class='collapse-array-value-sep'>,</span>}
              <span class='collapse-array-value-text'>{value}</span>
            </span>
          ),
          /** 折叠标识按设计稿呈现为 [+N] */
          collapseTag: (count: number) => <>[+{count}]</>,
        }}
        ellipsisTippyOptions={{
          theme: 'dark text-wrap max-width-50vw rum-collapse-array-ellipsis-tip',
        }}
        data={this.values}
        ellipsisTip={this.ellipsisTipRender}
      />
    );
  },
});
