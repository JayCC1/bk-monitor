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
 * documentation files (the "Software"), to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
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
import { type Ref, computed, shallowRef } from 'vue';

import { useDebounceFn } from '@vueuse/core';

import { useRumExploreStore } from '../../../store/modules/rum-explore';
import {
  DEFAULT_COLUMN_WIDTH,
  RUM_COLUMN_CONFIG_KEY,
  RUM_COLUMN_WIDTH_MAP,
  RUM_SORTABLE_FIELD_TYPES,
} from '../constants';
import useUserConfig from '@/hooks/useUserConfig';

import type { BaseTableColumn } from '../../trace-explore/components/trace-explore-table/typing';
import type { IRumViewConfig } from '../typings';

/** 列配置存储结构版本号，schema 变更时递增以自动失效旧缓存 */
const RUM_COLUMN_CONFIG_VERSION = '1.0.0';

/** useRumColumnConfig 返回的列配置上下文类型 */
export type IRumColumnConfig = ReturnType<typeof useRumColumnConfig>;

/** 常驻配置中存储的列配置结构 */
interface IRumColumnConfigCache {
  /** 列宽覆盖：colKey -> 宽度，覆盖常量默认值 */
  columnResizeWidth: Record<string, number>;
  /** 展示列的字段名（顺序即列顺序），同时表达显隐 */
  displayFields: string[];
  /** 配置版本号，用于清除过期缓存 */
  version?: string;
}

/**
 * 列配置集中管理 hook：统管列的显隐/顺序、列宽覆盖，并持久化到用户常驻配置。
 * 同时负责推导「可作为列的字段全集」与「当前 span 类型的默认列」。
 *
 * 设计参考 alarm-center/use-table-columns：将原始存储归一化为单一 columnConfigCache，
 * 并按有效字段键校验裁剪，避免脏字段/失效缓存渲染出错。
 *
 * 展示列派生规则：
 * - 指定 span 类型：直接展示该类型默认列（span_type_display_fields[type]），忽略用户缓存，且不允许设置列；
 * - 全部（spanType 为空）：用户缓存列 > 接口默认列（viewConfig.display_fields）。
 * 列宽与展示列的持久化仅在「全部」场景下生效（指定类型时列由类型决定、不落盘）。
 */
export function useRumColumnConfig(opts: {
  /** 缓存 key，默认 RUM_COLUMN_CONFIG_KEY */
  cacheKey?: string;
  /** 视图配置：用于推导可作为列的字段全集与当前类型的默认列 */
  viewConfig: Ref<IRumViewConfig>;
}) {
  const cacheKey = opts.cacheKey ?? RUM_COLUMN_CONFIG_KEY;
  const store = useRumExploreStore();
  const { handleGetUserConfig, handleSetUserConfig } = useUserConfig();

  /** 可作为列的字段全集，供字段设置使用 */
  const displayableFields = computed(() => opts.viewConfig.value.fields.filter(field => field.can_displayed));

  /** 字段名 -> 字段元数据；同时承担「有效字段集合」的校验职责 */
  const fieldMap = computed(() => new Map(displayableFields.value.map(field => [field.name, field])));

  /** 是否显示列设置：指定 span 类型时列由类型决定、忽略缓存，不允许用户设置 */
  const showSettings = computed(() => !store.spanType);

  /** 当前 span 类型对应的默认列（仅 span 视图、指定类型时有值） */
  const spanTypeDisplayFields = computed<string[]>(() => {
    if (!store.spanType) return [];
    return opts.viewConfig.value.span_type_display_fields?.[store.spanType] ?? [];
  });

  /** 接口默认展示列（全部场景的兜底） */
  const defaultDisplayFields = computed(() => opts.viewConfig.value.display_fields);

  /** 归一化后的缓存配置（始终为 IRumColumnConfigCache，按有效字段裁剪、版本失效回退默认） */
  const columnConfigCache = shallowRef<IRumColumnConfigCache>({
    displayFields: [],
    columnResizeWidth: {},
    version: RUM_COLUMN_CONFIG_VERSION,
  });

  /** 用户缓存的展示列（仅「全部」场景生效，可被接口默认列兜底；已按有效字段裁剪） */
  const storageColumns = computed<string[]>({
    get: () => {
      const cached = columnConfigCache.value.displayFields;
      const result = cached?.length ? cached : defaultDisplayFields.value;
      return result.filter(name => fieldMap.value.has(name));
    },
    set: (val: string[]) => {
      columnConfigCache.value = {
        ...columnConfigCache.value,
        displayFields: val.filter(name => fieldMap.value.has(name)),
      };
      saveColumnConfig();
    },
  });

  /** 列宽覆盖（已按有效字段裁剪掉不存在的列） */
  const fieldsWidthConfig = computed<Record<string, number>>({
    get: () => {
      const stored = columnConfigCache.value.columnResizeWidth ?? {};
      return Object.fromEntries(Object.entries(stored).filter(([key]) => fieldMap.value.has(key)));
    },
    set: (val: Record<string, number>) => {
      columnConfigCache.value = {
        ...columnConfigCache.value,
        columnResizeWidth: { ...fieldsWidthConfig.value, ...val },
      };
      saveColumnConfig();
    },
  });

  /** 生效的展示列（渲染与收藏使用） */
  const displayFields = computed<string[]>(() => {
    if (store.spanType) {
      // 指定类型：直接展示该类型默认列，不考虑用户缓存
      return spanTypeDisplayFields.value;
    }
    // 全部：用户缓存列 > 接口默认列
    return storageColumns.value;
  });

  /** 基础列配置：展示列 -> 列宽（列宽覆盖优先）-> 排序等元数据 */
  const baseColumns = computed<BaseTableColumn[]>(() =>
    displayFields.value
      .map(name => fieldMap.value.get(name))
      .filter(Boolean)
      .map(field => ({
        colKey: field.name,
        width: fieldsWidthConfig.value[field.name] ?? RUM_COLUMN_WIDTH_MAP[field.name] ?? DEFAULT_COLUMN_WIDTH,
        minWidth: 100,
        resizable: true,
        sorter: RUM_SORTABLE_FIELD_TYPES.has(field.type),
      }))
  );

  /**
   * 防抖保存列配置到用户常驻配置。
   * 仅在允许用户设置列时真正落盘（指定 span 类型时跳过）。
   */
  const saveColumnConfig = useDebounceFn(() => {
    if (!showSettings.value) return;
    handleSetUserConfig(JSON.stringify(columnConfigCache.value));
  }, 300);

  function updateDisplayFields(fields: string[]) {
    storageColumns.value = fields;
  }

  function updateColumnResizeWidth(width: Record<string, number>) {
    fieldsWidthConfig.value = width;
  }

  async function loadColumnConfig() {
    let cached: IRumColumnConfigCache | undefined;
    try {
      cached = await handleGetUserConfig<IRumColumnConfigCache>(cacheKey);
    } catch {
      cached = undefined;
    }
    // 版本不匹配或无有效缓存：丢弃并回退默认，待用户操作后再落盘
    const isVersionValid = cached?.version === RUM_COLUMN_CONFIG_VERSION;
    if (isVersionValid && cached?.displayFields?.length) {
      columnConfigCache.value = {
        displayFields: cached.displayFields,
        columnResizeWidth: cached.columnResizeWidth ?? {},
        version: RUM_COLUMN_CONFIG_VERSION,
      };
    }
  }

  return {
    displayFields,
    columnResizeWidth: fieldsWidthConfig,
    baseColumns,
    displayableFields,
    spanTypeDisplayFields,
    showSettings,
    updateDisplayFields,
    updateColumnResizeWidth,
    loadColumnConfig,
  };
}
