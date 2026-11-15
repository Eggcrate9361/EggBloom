#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
表格组件 - 现代化数据表格，支持排序、筛选、分页
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional, Callable, Dict, Any, List, Tuple, Union
from design_system import C, S, T, I

class DataTable(ttk.Treeview):
    """现代化数据表格"""
    
    def __init__(
        self,
        parent,
        columns: List[Dict[str, Any]],
        data: Optional[List[Dict[str, Any]]] = None,
        show_header: bool = True,
        show_footer: bool = False,
        selectable: bool = True,
        multi_select: bool = False,
        sortable: bool = True,
        filterable: bool = True,
        pagination: bool = False,
        page_size: int = 20,
        on_row_click: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_row_double_click: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_selection_change: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        **kwargs
    ):
        # 基础配置
        style = ttk.Style()
        style.configure(
            "Custom.Treeview",
            background=C["bg_card"],
            foreground=C["text_primary"],
            fieldbackground=C["bg_card"],
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Custom.Treeview.Heading",
            background=C["bg_elevated"],
            foreground=C["text_secondary"],
            relief="flat",
            borderwidth=0,
            font=(T.get_font_family(), T.get_font_config("body_small")["size"], "bold"),
        )
        style.map(
            "Custom.Treeview.Heading",
            background=[("active", C["hover_bg"])],
        )
        
        # 调用父类初始化
        super().__init__(parent, style="Custom.Treeview", **kwargs)
        
        # 属性
        self.columns_config = columns
        self.original_data = data or []
        self.filtered_data = self.original_data.copy()
        self.show_header = show_header
        self.show_footer = show_footer
        self.selectable = selectable
        self.multi_select = multi_select
        self.sortable = sortable
        self.filterable = filterable
        self.pagination = pagination
        self.page_size = page_size
        self.on_row_click = on_row_click
        self.on_row_double_click = on_row_double_click
        self.on_selection_change = on_selection_change
        
        # 状态
        self.current_page = 1
        self.total_pages = 1
        self.sort_column = None
        self.sort_direction = "asc"  # "asc" or "desc"
        self.filters = {}
        self.selected_rows = []
        
        # 配置表格
        self._configure_table()
        
        # 加载数据
        if data:
            self.load_data(data)
        
        # 绑定事件
        self._bind_events()
    
    def _configure_table(self):
        """配置表格"""
        # 设置列
        display_columns = []
        for col in self.columns_config:
            col_id = col.get("id", col["key"])
            col_text = col.get("title", col["key"])
            col_width = col.get("width", 100)
            col_align = col.get("align", "w")  # w, e, center
            
            display_columns.append(col_id)
            
            # 配置列
            self.column(col_id, width=col_width, anchor=col_align)
            
            # 配置表头
            if self.show_header:
                self.heading(
                    col_id,
                    text=col_text,
                    command=lambda c=col_id: self._on_header_click(c) if self.sortable else None,
                )
        
        # 设置显示列
        self["columns"] = display_columns
        
        # 隐藏第一列（Treeview默认的#0列）
        self.column("#0", width=0, stretch=False)
        
        # 配置标签
        self.tag_configure("oddrow", background=C["bg_card"])
        self.tag_configure("evenrow", background=C["bg_elevated"])
        self.tag_configure("selected", background=C["selected_bg"])
        
        # 配置选择模式
        if self.selectable:
            if self.multi_select:
                self["selectmode"] = "extended"
            else:
                self["selectmode"] = "browse"
        else:
            self["selectmode"] = "none"
    
    def _bind_events(self):
        """绑定事件"""
        # 行点击事件
        self.bind("<ButtonRelease-1>", self._on_click)
        self.bind("<Double-Button-1>", self._on_double_click)
        
        # 选择变化事件
        self.bind("<<TreeviewSelect>>", self._on_selection_change)
        
        # 键盘事件
        self.bind("<KeyPress>", self._on_key_press)
    
    def _on_header_click(self, column_id):
        """表头点击事件 - 排序"""
        if not self.sortable:
            return
        
        # 切换排序方向
        if self.sort_column == column_id:
            self.sort_direction = "desc" if self.sort_direction == "asc" else "asc"
        else:
            self.sort_column = column_id
            self.sort_direction = "asc"
        
        # 更新表头显示
        self._update_header_indicators()
        
        # 重新排序数据
        self._sort_data()
        
        # 重新渲染表格
        self._render_data()
    
    def _on_click(self, event):
        """行点击事件"""
        if not self.selectable:
            return
        
        # 获取点击的行
        row_id = self.identify_row(event.y)
        if not row_id:
            return
        
        # 获取行数据
        row_index = self.index(row_id)
        if 0 <= row_index < len(self.filtered_data):
            row_data = self.filtered_data[row_index]
            
            if self.on_row_click:
                self.on_row_click(row_data)
    
    def _on_double_click(self, event):
        """行双击事件"""
        if not self.selectable:
            return
        
        # 获取点击的行
        row_id = self.identify_row(event.y)
        if not row_id:
            return
        
        # 获取行数据
        row_index = self.index(row_id)
        if 0 <= row_index < len(self.filtered_data):
            row_data = self.filtered_data[row_index]
            
            if self.on_row_double_click:
                self.on_row_double_click(row_data)
    
    def _on_selection_change(self, event):
        """选择变化事件"""
        if not self.selectable:
            return
        
        # 获取选中的行
        selected_items = self.selection()
        self.selected_rows = []
        
        for item in selected_items:
            row_index = self.index(item)
            if 0 <= row_index < len(self.filtered_data):
                self.selected_rows.append(self.filtered_data[row_index])
        
        # 更新行样式
        self._update_row_styles()
        
        # 触发回调
        if self.on_selection_change:
            self.on_selection_change(self.selected_rows)
    
    def _on_key_press(self, event):
        """键盘事件"""
        # 支持Ctrl+A全选
        if event.state & 0x4 and event.keysym.lower() == "a":  # Ctrl+A
            if self.multi_select:
                self.selection_set(self.get_children())
            return "break"
    
    def _update_header_indicators(self):
        """更新表头排序指示器"""
        if not self.show_header:
            return
        
        for col in self.columns_config:
            col_id = col.get("id", col["key"])
            col_text = col.get("title", col["key"])
            
            if col_id == self.sort_column:
                indicator = " ▲" if self.sort_direction == "asc" else " ▼"
                self.heading(col_id, text=col_text + indicator)
            else:
                self.heading(col_id, text=col_text)
    
    def _sort_data(self):
        """排序数据"""
        if not self.sort_column:
            return
        
        # 查找排序列配置
        sort_col_config = None
        for col in self.columns_config:
            if col.get("id", col["key"]) == self.sort_column:
                sort_col_config = col
                break
        
        if not sort_col_config:
            return
        
        sort_key = sort_col_config["key"]
        sort_type = sort_col_config.get("type", "string")
        
        # 定义排序函数
        def sort_func(item):
            value = item.get(sort_key)
            
            # 根据类型处理
            if sort_type == "number":
                try:
                    return float(value) if value is not None else 0
                except (ValueError, TypeError):
                    return 0
            elif sort_type == "date":
                # 这里可以添加日期解析逻辑
                return str(value) if value is not None else ""
            else:  # string
                return str(value) if value is not None else ""
        
        # 排序
        self.filtered_data.sort(key=sort_func, reverse=(self.sort_direction == "desc"))
    
    def _apply_filters(self):
        """应用筛选器"""
        if not self.filters:
            self.filtered_data = self.original_data.copy()
            return
        
        self.filtered_data = []
        
        for item in self.original_data:
            match = True
            
            for col_key, filter_value in self.filters.items():
                if not filter_value:  # 空筛选条件跳过
                    continue
                
                item_value = item.get(col_key, "")
                
                # 字符串筛选（包含匹配）
                if isinstance(filter_value, str):
                    if filter_value.lower() not in str(item_value).lower():
                        match = False
                        break
                # 函数筛选
                elif callable(filter_value):
                    if not filter_value(item_value):
                        match = False
                        break
            
            if match:
                self.filtered_data.append(item)
    
    def _calculate_pagination(self):
        """计算分页"""
        if not self.pagination:
            self.total_pages = 1
            return
        
        total_items = len(self.filtered_data)
        self.total_pages = max(1, (total_items + self.page_size - 1) // self.page_size)
        
        # 确保当前页有效
        if self.current_page > self.total_pages:
            self.current_page = self.total_pages
    
    def _get_page_data(self):
        """获取当前页数据"""
        if not self.pagination:
            return self.filtered_data
        
        start_idx = (self.current_page - 1) * self.page_size
        end_idx = start_idx + self.page_size
        
        return self.filtered_data[start_idx:end_idx]
    
    def _render_data(self):
        """渲染数据到表格"""
        # 清空现有数据
        for item in self.get_children():
            self.delete(item)
        
        # 获取当前页数据
        page_data = self._get_page_data()
        
        # 插入数据
        for i, row_data in enumerate(page_data):
            values = []
            
            for col in self.columns_config:
                col_key = col["key"]
                value = row_data.get(col_key, "")
                
                # 格式化值
                formatter = col.get("formatter")
                if formatter and callable(formatter):
                    value = formatter(value, row_data)
                
                values.append(value)
            
            # 交替行背景色
            tags = ("evenrow",) if i % 2 == 0 else ("oddrow",)
            
            # 检查是否选中
            if row_data in self.selected_rows:
                tags = tags + ("selected",)
            
            # 插入行
            self.insert("", tk.END, values=values, tags=tags)
    
    def _update_row_styles(self):
        """更新行样式"""
        # 获取所有行
        children = self.get_children()
        
        for i, child in enumerate(children):
            row_index = (self.current_page - 1) * self.page_size + i
            if 0 <= row_index < len(self.filtered_data):
                row_data = self.filtered_data[row_index]
                
                # 确定标签
                tags = ("evenrow",) if i % 2 == 0 else ("oddrow",)
                if row_data in self.selected_rows:
                    tags = tags + ("selected",)
                
                # 更新标签
                self.item(child, tags=tags)
    
    def load_data(self, data: List[Dict[str, Any]]):
        """加载数据"""
        self.original_data = data.copy()
        
        # 应用筛选
        self._apply_filters()
        
        # 排序
        if self.sort_column:
            self._sort_data()
        
        # 计算分页
        self._calculate_pagination()
        
        # 渲染数据
        self._render_data()
    
    def add_row(self, row_data: Dict[str, Any]):
        """添加行"""
        self.original_data.append(row_data)
        
        # 重新加载数据
        self.load_data(self.original_data)
    
    def update_row(self, index: int, row_data: Dict[str, Any]):
        """更新行"""
        if 0 <= index < len(self.original_data):
            self.original_data[index] = row_data
            
            # 重新加载数据
            self.load_data(self.original_data)
    
    def delete_row(self, index: int):
        """删除行"""
        if 0 <= index < len(self.original_data):
            del self.original_data[index]
            
            # 重新加载数据
            self.load_data(self.original_data)
    
    def set_filter(self, column_key: str, filter_value: Union[str, Callable]):
        """设置筛选器"""
        self.filters[column_key] = filter_value
        self._apply_filters()
        self._calculate_pagination()
        self._render_data()
    
    def clear_filters(self):
        """清空筛选器"""
        self.filters.clear()
        self._apply_filters()
        self._calculate_pagination()
        self._render_data()
    
    def set_page(self, page: int):
        """设置当前页"""
        if self.pagination and 1 <= page <= self.total_pages:
            self.current_page = page
            self._render_data()
    
    def next_page(self):
        """下一页"""
        if self.pagination and self.current_page < self.total_pages:
            self.current_page += 1
            self._render_data()
    
    def prev_page(self):
        """上一页"""
        if self.pagination and self.current_page > 1:
            self.current_page -= 1
            self._render_data()
    
    def get_selected_rows(self) -> List[Dict[str, Any]]:
        """获取选中的行"""
        return self.selected_rows.copy()
    
    def clear_selection(self):
        """清空选择"""
        self.selection_remove(self.selection())
        self.selected_rows = []
        self._update_row_styles()
    
    def refresh(self):
        """刷新表格"""
        self.load_data(self.original_data)


class TableWithControls(tk.Frame):
    """带控件的表格 - 包含筛选、分页等控制功能"""
    
    def __init__(
        self,
        parent,
        columns: List[Dict[str, Any]],
        data: Optional[List[Dict[str, Any]]] = None,
        title: str = "",
        show_search: bool = True,
        show_filters: bool = True,
        show_pagination: bool = True,
        show_actions: bool = True,
        actions: Optional[List[Tuple[str, Callable]]] = None,
        **table_kwargs
    ):
        super().__init__(parent, bg=C["bg_root"])
        
        # 属性
        self.columns = columns
        self.data = data or []
        self.title = title
        self.show_search = show_search
        self.show_filters = show_filters
        self.show_pagination = show_pagination
        self.show_actions = show_actions
        self.actions = actions or []
        
        # 创建UI
        self._create_ui(**table_kwargs)
    
    def _create_ui(self, **table_kwargs):
        """创建UI"""
        # 标题栏
        if self.title:
            title_frame = tk.Frame(self, bg=C["bg_root"])
            title_frame.pack(fill=tk.X, pady=(0, S.get("lg")))
            
            title_label = tk.Label(
                title_frame,
                text=self.title,
                font=(
                    T.get_font_family(),
                    T.get_font_config("heading_medium")["size"],
                    "bold",
                ),
                fg=C["text_primary"],
                bg=C["bg_root"],
            )
            title_label.pack(side=tk.LEFT)
        
        # 控制栏
        control_frame = tk.Frame(self, bg=C["bg_root"])
        control_frame.pack(fill=tk.X, pady=(0, S.get("md")))
        
        # 左侧：搜索和筛选
        left_control = tk.Frame(control_frame, bg=C["bg_root"])
        left_control.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # 搜索框
        if self.show_search:
            self._create_search_box(left_control)
        
        # 筛选按钮
        if self.show_filters:
            self._create_filter_button(left_control)
        
        # 右侧：操作按钮
        right_control = tk.Frame(control_frame, bg=C["bg_root"])
        right_control.pack(side=tk.RIGHT)
        
        # 操作按钮
        if self.show_actions and self.actions:
            self._create_action_buttons(right_control)
        
        # 刷新按钮
        refresh_btn = tk.Label(
            right_control,
            text=I.get("refresh"),
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_secondary"],
            bg=C["bg_root"],
            cursor="hand2",
            padx=S.get("sm"),
        )
        refresh_btn.pack(side=tk.LEFT, padx=(S.get("lg"), 0))
        refresh_btn.bind("<Button-1>", lambda e: self.table.refresh())
        
        # 表格
        table_container = tk.Frame(self, bg=C["bg_root"])
        table_container.pack(fill=tk.BOTH, expand=True)
        
        # 创建表格
        self.table = DataTable(
            table_container,
            columns=self.columns,
            data=self.data,
            **table_kwargs
        )
        
        # 添加滚动条
        scrollbar = ttk.Scrollbar(table_container, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        
        # 布局
        self.table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 分页控件
        if self.show_pagination and table_kwargs.get("pagination", False):
            self._create_pagination_controls()
    
    def _create_search_box(self, parent):
        """创建搜索框"""
        search_frame = tk.Frame(parent, bg=C["bg_root"])
        search_frame.pack(side=tk.LEFT, padx=(0, S.get("md")))
        
        # 搜索图标
        search_icon = tk.Label(
            search_frame,
            text=I.get("search"),
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_tertiary"],
            bg=C["bg_root"],
        )
        search_icon.pack(side=tk.LEFT, padx=(S.get("sm"), 0))
        
        # 搜索输入框
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            search_frame,
            textvariable=self.search_var,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            bg=C["bg_input"],
            fg=C["text_primary"],
            insertbackground=C["accent_primary"],
            relief=tk.FLAT,
            bd=0,
            width=20,
        )
        self.search_entry.pack(side=tk.LEFT, padx=(S.get("sm"), S.get("sm")))
        self.search_entry.insert(0, "搜索...")
        
        # 绑定事件
        self.search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self.search_entry.bind("<FocusOut>", self._on_search_focus_out)
        self.search_entry.bind("<Return>", self._on_search_enter)
        
        # 搜索按钮
        search_btn = tk.Label(
            search_frame,
            text=I.get("search"),
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_secondary"],
            bg=C["bg_root"],
            cursor="hand2",
        )
        search_btn.pack(side=tk.LEFT)
        search_btn.bind("<Button-1>", self._on_search_click)
    
    def _create_filter_button(self, parent):
        """创建筛选按钮"""
        filter_btn = tk.Label(
            parent,
            text=f"{I.get('filter')} 筛选",
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_secondary"],
            bg=C["bg_root"],
            cursor="hand2",
            padx=S.get("sm"),
            pady=S.get("xs"),
        )
        filter_btn.pack(side=tk.LEFT)
        filter_btn.bind("<Button-1>", self._on_filter_click)
    
    def _create_action_buttons(self, parent):
        """创建操作按钮"""
        for text, callback in self.actions:
            btn = tk.Label(
                parent,
                text=text,
                font=(T.get_font_family(), T.get_font_config("button_small")["size"]),
                fg=C["accent_primary"],
                bg=C["bg_elevated"],
                cursor="hand2",
                padx=S.get("md"),
                pady=S.get("xs"),
                relief=tk.FLAT,
            )
            btn.pack(side=tk.LEFT, padx=(0, S.get("sm")))
            btn.bind("<Button-1>", lambda e, cb=callback: cb())
            
            # 悬停效果
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=C["hover_bg"]))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=C["bg_elevated"]))
    
    def _create_pagination_controls(self):
        """创建分页控件"""
        pagination_frame = tk.Frame(self, bg=C["bg_root"])
        pagination_frame.pack(fill=tk.X, pady=(S.get("md"), 0))
        
        # 左侧：页面信息
        info_frame = tk.Frame(pagination_frame, bg=C["bg_root"])
        info_frame.pack(side=tk.LEFT)
        
        self.page_info = tk.Label(
            info_frame,
            text="第 1 页 / 共 1 页",
            font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
            fg=C["text_secondary"],
            bg=C["bg_root"],
        )
        self.page_info.pack()
        
        # 右侧：分页按钮
        button_frame = tk.Frame(pagination_frame, bg=C["bg_root"])
        button_frame.pack(side=tk.RIGHT)
        
        # 上一页
        prev_btn = tk.Label(
            button_frame,
            text="◀",
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_secondary"],
            bg=C["bg_root"],
            cursor="hand2",
            padx=S.get("sm"),
        )
        prev_btn.pack(side=tk.LEFT)
        prev_btn.bind("<Button-1>", lambda e: self.table.prev_page())
        
        # 下一页
        next_btn = tk.Label(
            button_frame,
            text="▶",
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_secondary"],
            bg=C["bg_root"],
            cursor="hand2",
            padx=S.get("sm"),
        )
        next_btn.pack(side=tk.LEFT)
        next_btn.bind("<Button-1>", lambda e: self.table.next_page())
        
        # 更新页面信息
        self._update_page_info()
    
    def _on_search_focus_in(self, event):
        """搜索框获得焦点"""
        if self.search_entry.get() == "搜索...":
            self.search_entry.delete(0, tk.END)
            self.search_entry.configure(fg=C["text_primary"])
    
    def _on_search_focus_out(self, event):
        """搜索框失去焦点"""
        if not self.search_entry.get():
            self.search_entry.insert(0, "搜索...")
            self.search_entry.configure(fg=C["text_tertiary"])
    
    def _on_search_enter(self, event):
        """搜索框回车事件"""
        self._perform_search()
    
    def _on_search_click(self, event):
        """搜索按钮点击事件"""
        self._perform_search()
    
    def _on_filter_click(self, event):
        """筛选按钮点击事件"""
        # 这里可以打开筛选面板
        print("打开筛选面板")
    
    def _perform_search(self):
        """执行搜索"""
        query = self.search_entry.get()
        if query and query != "搜索...":
            # 简单实现：在所有列中搜索
            def search_filter(value):
                return query.lower() in str(value).lower()
            
            # 应用筛选
            for col in self.columns:
                self.table.set_filter(col["key"], search_filter)
        else:
            # 清除筛选
            self.table.clear_filters()
    
    def _update_page_info(self):
        """更新页面信息"""
        if hasattr(self, 'page_info') and self.table.pagination:
            info = f"第 {self.table.current_page} 页 / 共 {self.table.total_pages} 页"
            self.page_info.configure(text=info)
    
    def load_data(self, data: List[Dict[str, Any]]):
        """加载数据"""
        self.table.load_data(data)
        self._update_page_info()
    
    def refresh(self):
        """刷新"""
        self.table.refresh()
        self._update_page_info()
    
    def get_table(self) -> DataTable:
        """获取内部表格对象"""
        return self.table


if __name__ == "__main__":
    # 测试表格组件
    root = tk.Tk()
    root.title("Table Component Test")
    root.geometry("1200x600")
    root.configure(bg=C["bg_root"])
    
    # 测试数据
    test_columns = [
        {"key": "id", "title": "ID", "width": 60, "type": "number", "align": "center"},
        {"key": "name", "title": "名称", "width": 150},
        {"key": "type", "title": "类型", "width": 100},
        {"key": "status", "title": "状态", "width": 100, "align": "center"},
        {"key": "created", "title": "创建时间", "width": 150},
        {"key": "size", "title": "大小", "width": 100, "type": "number", "align": "right"},
    ]
    
    test_data = [
        {"id": 1, "name": "项目文档", "type": "文档", "status": "正常", "created": "2024-01-15", "size": 1024},
        {"id": 2, "name": "设计稿", "type": "图片", "status": "正常", "created": "2024-01-16", "size": 2048},
        {"id": 3, "name": "代码文件", "type": "代码", "status": "警告", "created": "2024-01-17", "size": 5120},
        {"id": 4, "name": "配置文件", "type": "配置", "status": "正常", "created": "2024-01-18", "size": 256},
        {"id": 5, "name": "日志文件", "type": "日志", "status": "错误", "created": "2024-01-19", "size": 10240},
        {"id": 6, "name": "备份文件", "type": "备份", "status": "正常", "created": "2024-01-20", "size": 5120},
        {"id": 7, "name": "测试数据", "type": "数据", "status": "正常", "created": "2024-01-21", "size": 1024},
        {"id": 8, "name": "用户数据", "type": "数据", "status": "警告", "created": "2024-01-22", "size": 2048},
        {"id": 9, "name": "系统文件", "type": "系统", "status": "正常", "created": "2024-01-23", "size": 512},
        {"id": 10, "name": "临时文件", "type": "临时", "status": "正常", "created": "2024-01-24", "size": 128},
    ]
    
    # 创建带控件的表格
    def on_row_click(row):
        print(f"行点击: {row}")
    
    def on_selection_change(rows):
        print(f"选择变化: 选择了{len(rows)}行")
    
    def export_data():
        print("导出数据")
    
    def delete_selected():
        print("删除选中")
    
    table = TableWithControls(
        root,
        columns=test_columns,
        data=test_data,
        title="文件列表",
        show_search=True,
        show_filters=True,
        show_pagination=True,
        show_actions=True,
        actions=[
            ("导出", export_data),
            ("删除", delete_selected),
        ],
        selectable=True,
        multi_select=True,
        sortable=True,
        filterable=True,
        pagination=True,
        page_size=5,
        on_row_click=on_row_click,
        on_selection_change=on_selection_change,
    )
    table.pack(fill=tk.BOTH, expand=True, padx=S.get("lg"), pady=S.get("lg"))
    
    root.mainloop()