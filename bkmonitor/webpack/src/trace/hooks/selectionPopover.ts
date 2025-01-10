/*
 * Tencent is pleased to support the open source community by making
 * 蓝鲸智云PaaS平台 (BlueKing PaaS) available.
 *
 * Copyright (C) 2021 THL A29 Limited, a Tencent company.  All rights reserved.
 *
 * 蓝鲸智云PaaS平台 (BlueKing PaaS) is licensed under the MIT License.
 *
 * License for 蓝鲸智云PaaS平台 (BlueKing PaaS):
 *
 * ---------------------------------------------------
 * Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
 * documentation files (the "Software"), to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
 * to permit persons to whom the Software is furnished to do so, subject to the following conditions:
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

import { onBeforeUnmount, onMounted, type Ref, ref } from 'vue';

import { $bkPopover } from 'bkui-vue/lib/popover';
import { random } from 'bkui-vue/lib/shared';
import { debounce } from 'lodash';

import type { $Popover } from 'bkui-vue/lib/popover/plugin-popover';

type SelectionMarkSuccess = (selectionStr: string, selection: Selection, event: MouseEvent) => void;
type SelectionMarkLose = (selection: Selection, event: MouseEvent) => any;
interface SelectionProps {
  markSuccess: SelectionMarkSuccess;
  markLose?: SelectionMarkLose;
}

type PopoverInstance = {
  show: () => void;
  hide: () => void;
  close: () => void;
  [key: string]: any;
};

/**
 * @description 处理文本选择事件，提供了灵活的配置选项和回调机制
 * @param selector 需要监听 selectionchange 事件的元素，如不传则内部生成一个 className 并暴露出去，用户自定义使用处理
 * @param config.markSuccess 选区内容匹配成功时的回调
 * @param config.markLose 选区内容匹配失败时的回调
 */
function useSelection(config: SelectionProps);
function useSelection(selector: string, config: SelectionProps);
function useSelection(selector: SelectionProps | string, config?: SelectionProps) {
  /** 可以触发监听 selectionchange 事件的元素类名（包含其子级） */
  let selectionTriggerSelector: string;
  let selectionConfig: SelectionProps = config;
  // traceRootDocument 用于处理 shadowRoot 的情况
  let traceRootDocument: Document;
  if (typeof selector === 'string') {
    selectionTriggerSelector = selector;
  } else {
    selectionTriggerSelector = `.selection-trigger-node__${random(6)}`;
    selectionConfig = selector;
  }
  const { markSuccess, markLose = () => {} } = selectionConfig;

  /** selection 选区改变后触发回调事件 */
  const handleSelectionChange = debounce(e => {
    // 微前端环境时，会导致 document 获取的实际为父应用环境中的 document 对象，
    // 而 bk-weweb 会重新创建 document 对象，导致此处获取到的 document 不是当前页面的 document 对象
    if (!traceRootDocument) {
      traceRootDocument = window.__POWERED_BY_BK_WEWEB__ ? e.srcElement.shadowRoot : window.document;
    }
    const selection = traceRootDocument.getSelection();
    // 在微前端环境下，Selection.isCollapsed 会一直为 true 时，改用 offset 进行判断是否选中文本
    if (selection?.focusOffset === selection.anchorOffset) {
      return markLose(selection, e);
    }
    const range = selection.getRangeAt(0);
    const matchSourceEl =
      range.commonAncestorContainer.nodeName === '#text'
        ? range.commonAncestorContainer.parentNode
        : range.commonAncestorContainer;
    // @ts-ignore
    if (!matchSourceEl?.closest(`${selectionTriggerSelector}`)) {
      return markLose(selection, e);
    }
    markSuccess(selection.toString(), selection, e);
  }, 200);

  onMounted(() => {
    // 由于在微前端环境下，selectionChange 事件并不会触发，所以改用 mouseup 监听
    document.addEventListener('mouseup', handleSelectionChange);
  });

  onBeforeUnmount(() => {
    // 由于在微前端环境下，selectionChange 事件并不会触发，所以改用 mouseup 监听
    document.removeEventListener('mouseup', handleSelectionChange);
  });

  return {
    selectionTriggerSelector,
  };
}

/**
 * @description 选区气泡提示
 * @param content 气泡内容
 * @param popoverProp 气泡配置
 * @returns markText 选中的文本
 * @returns popoverInstance 气泡实例
 * @returns selectionTriggerSelector 可以触发监听 selectionchange 事件的元素类名（包含其子级）
 */
const useSelectionPopover = (content: Ref<HTMLElement>, popoverProp: Partial<$Popover>) => {
  /** 选区标记元素类名（用于弹窗定位） */
  const selectionMarkerClassName = `selection-popover-marker-${random(8)}`;
  /** 选区标记元素 */
  let selectionMarkerEl = null;
  /** 选中的文本 */
  const markText = ref('');
  /** 选区范围 */
  const markRange = ref(null);
  /** 气泡弹窗实例 */
  const popoverInstance = ref<PopoverInstance>();
  /** 气泡弹窗区域元素内需要阻止默认行为的事件 */
  const preventDefaultForPopoverEvents = ['click', 'mousedown', 'mouseup'];

  const { selectionTriggerSelector } = useSelection({
    markSuccess: (str, selection) => {
      if (popoverInstance.value) {
        resetPopover();
      }

      markText.value = str;
      markRange.value = selection.getRangeAt(0);
      const rects = markRange.value.getClientRects();
      const rect = rects[0];
      resetSelectionMarkerEl();
      selectionMarkerEl.style.left = `${rect.left}px`;
      selectionMarkerEl.style.top = `${rect.top}px`;
      selectionMarkerEl.style.width = `${rect.width}px`;
      selectionMarkerEl.style.height = `${rect.height}px`;

      popoverInstance.value = $bkPopover({
        target: selectionMarkerEl,
        content: content.value,
        arrow: true,
        trigger: 'manual',
        placement: 'top',
        theme: 'light',
        width: 'auto',
        disabled: false,
        isShow: true,
        always: false,
        height: 'auto',
        maxWidth: 'auto',
        maxHeight: 'auto',
        allowHtml: false,
        renderType: 'auto',
        padding: 0,
        offset: { mainAxis: 10, crossAxis: 0 },
        zIndex: 9999,
        disableTeleport: false,
        autoPlacement: false,
        autoVisibility: false,
        disableOutsideClick: false,
        disableTransform: false,
        modifiers: [],
        popoverDelay: 0,
        componentEventDelay: 0,
        forceClickoutside: false,
        immediate: false,
        ...popoverProp,
        extCls: `selection-popover ${popoverProp?.extCls || ''}`,
      });
      popoverInstance.value.install();
      setTimeout(() => {
        popoverInstance.value?.vm?.show();
        const popoverNode = popoverInstance.value?.$el?.parentNode?.nextElementSibling;
        preventDefaultForPopover(popoverNode);
      }, 100);
    },
    markLose: (selection, e) => {
      if (!popoverInstance.value) {
        return;
      }
      const popoverNode = popoverInstance.value?.$el?.parentNode?.nextElementSibling;
      if (popoverNode?.contains?.(e.target)) {
        return;
      }
      resetPopover();
    },
  });

  /** 阻止气泡弹窗区域事件默认行为（防止选区被删除） */
  function preventDefaultForPopover(target: Node) {
    if (target?.addEventListener) {
      for (const event of preventDefaultForPopoverEvents) {
        target.addEventListener(event, e => {
          e.preventDefault();
          e.stopPropagation();
        });
      }
    }
  }

  /** 重置气泡弹框及选区相关属性 */
  function resetPopover() {
    popoverInstance.value?.hide();
    popoverInstance.value?.close?.();
    popoverInstance.value = null;
    markText.value = '';
    markRange.value = null;
  }

  /** 创建选区标记元素 */
  function createSelectionMarkerEl() {
    selectionMarkerEl = document.createElement('span');
    selectionMarkerEl.style.setProperty('position', 'fixed');
    selectionMarkerEl.style.setProperty('z-index', '-1');
    selectionMarkerEl.style.setProperty('pointer-events', 'none');
    selectionMarkerEl.className = selectionMarkerClassName;
    document.body.appendChild(selectionMarkerEl);
    return selectionMarkerEl;
  }

  /** 重置选区标记元素 */
  function resetSelectionMarkerEl() {
    selectionMarkerEl.style.left = '0px';
    selectionMarkerEl.style.top = '0px';
    selectionMarkerEl.style.width = '0px';
    selectionMarkerEl.style.height = '0px';
  }

  /** 移除选区标记元素 */
  function removeSelectionMarkerEl() {
    if (!selectionMarkerEl) {
      return;
    }
    selectionMarkerEl?.remove();
    selectionMarkerEl = null;
  }

  onMounted(() => {
    createSelectionMarkerEl();
  });
  onBeforeUnmount(() => {
    removeSelectionMarkerEl();
  });
  return {
    markText,
    popoverInstance,
    selectionTriggerSelector,
  };
};

export { useSelection, useSelectionPopover };
