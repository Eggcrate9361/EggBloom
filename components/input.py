#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
输入组件 - 现代化表单输入控件
"""

import tkinter as tk
from typing import Optional, Callable, List
from design_system import C, S, T


class Input(tk.Frame):
    """现代化文本输入框"""
    
    def __init__(
        self,
        parent,
        label: str = "",
        placeholder: str = "",
        value: str = "",
        password: bool = False,
        disabled: bool = False,
        required: bool = False,
        error: str = "",
        helper_text: str = "",
        width: Optional[int] = None,
        on_change: Optional[Callable[[str], None]] = None,
        on_focus: Optional[Callable] = None,
        on_blur: Optional[Callable] = None,
        **kwargs
    ):
        bg = kwargs.pop("bg", C["bg_root"])
        super().__init__(parent, bg=bg, **kwargs)
        
        # 属性
        self.label_text = label
        self.placeholder = placeholder
        self.password = password
        self.disabled = disabled
        self.required = required
        self.error_text = error
        self.helper_text = helper_text
        self.on_change = on_change
        self.on_focus = on_focus
        self.on_blur = on_blur
        
        # 状态
        self._focused = False
        self._has_value = bool(value)
        
        # 创建内容
        self._create_content()
        
        # 设置初始值
        if value:
            self.set_value(value)
    
    def _create_content(self):
        """创建内容"""
        # 标签
        if self.label_text:
            label_frame = tk.Frame(self, bg=self["bg"])
            label_frame.pack(fill=tk.X, pady=(0, S.get("xs")))
            
            self.label = tk.Label(
                label_frame,
                text=self.label_text,
                font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
                fg=C["text_secondary"],
                bg=self["bg"],
                anchor=tk.W,
            )
            self.label.pack(side=tk.LEFT)
            
            # 必填标记
            if self.required:
                required_label = tk.Label(
                    label_frame,
                    text="*",
                    font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
                    fg=C["accent_danger"],
                    bg=self["bg"],
                )
                required_label.pack(side=tk.LEFT, padx=(2, 0))
        
        # 输入框容器
        input_container = tk.Frame(
            self,
            bg=C["bg_input"],
            highlightthickness=1,
            highlightbackground=C["border_light"],
        )
        input_container.pack(fill=tk.X)
        
        # 输入框
        show_char = "*" if self.password else ""
        self.entry = tk.Entry(
            input_container,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            bg=C["bg_input"],
            fg=C["text_primary"],
            insertbackground=C["accent_primary"],
            relief=tk.FLAT,
            bd=0,
            show=show_char,
            disabledforeground=C["text_tertiary"],
        )
        self.entry.pack(fill=tk.X, padx=S.get("md"), pady=S.get("sm"))
        
        # 占位符
        if self.placeholder:
            self.entry.insert(0, self.placeholder)
            self.entry.configure(fg=C["text_tertiary"])
        
        # 禁用状态
        if self.disabled:
            self.entry.configure(state="disabled")
        
        # 绑定事件
        self._bind_events()
        
        # 错误信息
        if self.error_text:
            self._show_error(self.error_text)
        
        # 辅助文本
        elif self.helper_text:
            self._show_helper(self.helper_text)
    
    def _bind_events(self):
        """绑定事件"""
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<KeyRelease>", self._on_key_release)
    
    def _on_focus_in(self, event):
        """获得焦点"""
        self._focused = True
        
        # 清除占位符
        if self.placeholder and self.entry.get() == self.placeholder:
            self.entry.delete(0, tk.END)
            self.entry.configure(fg=C["text_primary"])
        
        # 更新边框
        self._update_border()
        
        # 触发回调
        if self.on_focus:
            self.on_focus()
    
    def _on_focus_out(self, event):
        """失去焦点"""
        self._focused = False
        
        # 恢复占位符
        if self.placeholder and not self.entry.get():
            self.entry.insert(0, self.placeholder)
            self.entry.configure(fg=C["text_tertiary"])
        
        # 更新边框
        self._update_border()
        
        # 触发回调
        if self.on_blur:
            self.on_blur()
    
    def _on_key_release(self, event):
        """按键释放"""
        value = self.get_value()
        self._has_value = bool(value)
        
        # 触发回调
        if self.on_change:
            self.on_change(value)
    
    def _update_border(self):
        """更新边框"""
        if self.error_text:
            color = C["accent_danger"]
        elif self._focused:
            color = C["accent_primary"]
        else:
            color = C["border_light"]
        
        self.winfo_children()[1].configure(highlightbackground=color)
    
    def _show_error(self, message: str):
        """显示错误信息"""
        self.error_text = message
        
        # 移除旧的错误/辅助文本
        for child in self.winfo_children():
            if isinstance(child, tk.Label) and child != getattr(self, 'label', None):
                child.destroy()
        
        # 显示错误
        error_label = tk.Label(
            self,
            text=message,
            font=(T.get_font_family(), T.get_font_config("caption")["size"]),
            fg=C["accent_danger"],
            bg=self["bg"],
            anchor=tk.W,
        )
        error_label.pack(fill=tk.X, pady=(S.get("xs"), 0))
        
        # 更新边框
        self._update_border()
    
    def _show_helper(self, message: str):
        """显示辅助文本"""
        self.helper_text = message
        
        # 移除旧的错误/辅助文本
        for child in self.winfo_children():
            if isinstance(child, tk.Label) and child != getattr(self, 'label', None):
                child.destroy()
        
        # 显示辅助文本
        helper_label = tk.Label(
            self,
            text=message,
            font=(T.get_font_family(), T.get_font_config("caption")["size"]),
            fg=C["text_tertiary"],
            bg=self["bg"],
            anchor=tk.W,
        )
        helper_label.pack(fill=tk.X, pady=(S.get("xs"), 0))
    
    def get_value(self) -> str:
        """获取值"""
        value = self.entry.get()
        if value == self.placeholder:
            return ""
        return value
    
    def set_value(self, value: str):
        """设置值"""
        self.entry.delete(0, tk.END)
        self.entry.insert(0, value)
        self.entry.configure(fg=C["text_primary"])
        self._has_value = bool(value)
    
    def set_error(self, message: str):
        """设置错误信息"""
        self._show_error(message)
    
    def clear_error(self):
        """清除错误信息"""
        self.error_text = ""
        
        # 移除错误/辅助文本
        for child in self.winfo_children():
            if isinstance(child, tk.Label) and child != getattr(self, 'label', None):
                child.destroy()
        
        # 恢复辅助文本
        if self.helper_text:
            self._show_helper(self.helper_text)
        
        # 更新边框
        self._update_border()
    
    def set_disabled(self, disabled: bool):
        """设置禁用状态"""
        self.disabled = disabled
        if disabled:
            self.entry.configure(state="disabled")
        else:
            self.entry.configure(state="normal")
    
    def focus(self):
        """聚焦"""
        self.entry.focus_set()


class TextArea(tk.Frame):
    """现代化多行文本输入框"""
    
    def __init__(
        self,
        parent,
        label: str = "",
        placeholder: str = "",
        value: str = "",
        disabled: bool = False,
        required: bool = False,
        rows: int = 4,
        error: str = "",
        helper_text: str = "",
        on_change: Optional[Callable[[str], None]] = None,
        **kwargs
    ):
        bg = kwargs.pop("bg", C["bg_root"])
        super().__init__(parent, bg=bg, **kwargs)
        
        # 属性
        self.label_text = label
        self.placeholder = placeholder
        self.disabled = disabled
        self.required = required
        self.rows = rows
        self.error_text = error
        self.helper_text = helper_text
        self.on_change = on_change
        
        # 状态
        self._focused = False
        
        # 创建内容
        self._create_content()
        
        # 设置初始值
        if value:
            self.set_value(value)
    
    def _create_content(self):
        """创建内容"""
        # 标签
        if self.label_text:
            label_frame = tk.Frame(self, bg=self["bg"])
            label_frame.pack(fill=tk.X, pady=(0, S.get("xs")))
            
            self.label = tk.Label(
                label_frame,
                text=self.label_text,
                font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
                fg=C["text_secondary"],
                bg=self["bg"],
                anchor=tk.W,
            )
            self.label.pack(side=tk.LEFT)
            
            if self.required:
                required_label = tk.Label(
                    label_frame,
                    text="*",
                    font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
                    fg=C["accent_danger"],
                    bg=self["bg"],
                )
                required_label.pack(side=tk.LEFT, padx=(2, 0))
        
        # 文本区域容器
        text_container = tk.Frame(
            self,
            bg=C["bg_input"],
            highlightthickness=1,
            highlightbackground=C["border_light"],
        )
        text_container.pack(fill=tk.X)
        
        # 文本区域
        self.text = tk.Text(
            text_container,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            bg=C["bg_input"],
            fg=C["text_primary"],
            insertbackground=C["accent_primary"],
            relief=tk.FLAT,
            bd=0,
            height=self.rows,
            wrap=tk.WORD,
        )
        self.text.pack(fill=tk.X, padx=S.get("md"), pady=S.get("sm"))
        
        # 占位符
        if self.placeholder:
            self.text.insert("1.0", self.placeholder)
            self.text.configure(fg=C["text_tertiary"])
        
        # 禁用状态
        if self.disabled:
            self.text.configure(state="disabled")
        
        # 绑定事件
        self._bind_events()
        
        # 错误/辅助文本
        if self.error_text:
            self._show_error(self.error_text)
        elif self.helper_text:
            self._show_helper(self.helper_text)
    
    def _bind_events(self):
        """绑定事件"""
        self.text.bind("<FocusIn>", self._on_focus_in)
        self.text.bind("<FocusOut>", self._on_focus_out)
        self.text.bind("<KeyRelease>", self._on_key_release)
    
    def _on_focus_in(self, event):
        """获得焦点"""
        self._focused = True
        
        # 清除占位符
        if self.placeholder and self.text.get("1.0", "end-1c") == self.placeholder:
            self.text.delete("1.0", tk.END)
            self.text.configure(fg=C["text_primary"])
        
        # 更新边框
        self._update_border()
    
    def _on_focus_out(self, event):
        """失去焦点"""
        self._focused = False
        
        # 恢复占位符
        if self.placeholder and not self.text.get("1.0", "end-1c"):
            self.text.insert("1.0", self.placeholder)
            self.text.configure(fg=C["text_tertiary"])
        
        # 更新边框
        self._update_border()
    
    def _on_key_release(self, event):
        """按键释放"""
        if self.on_change:
            self.on_change(self.get_value())
    
    def _update_border(self):
        """更新边框"""
        if self.error_text:
            color = C["accent_danger"]
        elif self._focused:
            color = C["accent_primary"]
        else:
            color = C["border_light"]
        
        self.winfo_children()[1].configure(highlightbackground=color)
    
    def _show_error(self, message: str):
        """显示错误信息"""
        self.error_text = message
        
        for child in self.winfo_children():
            if isinstance(child, tk.Label) and child != getattr(self, 'label', None):
                child.destroy()
        
        error_label = tk.Label(
            self,
            text=message,
            font=(T.get_font_family(), T.get_font_config("caption")["size"]),
            fg=C["accent_danger"],
            bg=self["bg"],
            anchor=tk.W,
        )
        error_label.pack(fill=tk.X, pady=(S.get("xs"), 0))
        
        self._update_border()
    
    def _show_helper(self, message: str):
        """显示辅助文本"""
        self.helper_text = message
        
        for child in self.winfo_children():
            if isinstance(child, tk.Label) and child != getattr(self, 'label', None):
                child.destroy()
        
        helper_label = tk.Label(
            self,
            text=message,
            font=(T.get_font_family(), T.get_font_config("caption")["size"]),
            fg=C["text_tertiary"],
            bg=self["bg"],
            anchor=tk.W,
        )
        helper_label.pack(fill=tk.X, pady=(S.get("xs"), 0))
    
    def get_value(self) -> str:
        """获取值"""
        value = self.text.get("1.0", "end-1c")
        if value == self.placeholder:
            return ""
        return value
    
    def set_value(self, value: str):
        """设置值"""
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", value)
        self.text.configure(fg=C["text_primary"])
    
    def set_error(self, message: str):
        """设置错误信息"""
        self._show_error(message)
    
    def clear_error(self):
        """清除错误信息"""
        self.error_text = ""
        
        for child in self.winfo_children():
            if isinstance(child, tk.Label) and child != getattr(self, 'label', None):
                child.destroy()
        
        if self.helper_text:
            self._show_helper(self.helper_text)
        
        self._update_border()
    
    def set_disabled(self, disabled: bool):
        """设置禁用状态"""
        self.disabled = disabled
        if disabled:
            self.text.configure(state="disabled")
        else:
            self.text.configure(state="normal")


class Select(tk.Frame):
    """现代化下拉选择框"""
    
    def __init__(
        self,
        parent,
        label: str = "",
        options: List[str] = None,
        value: str = "",
        disabled: bool = False,
        required: bool = False,
        error: str = "",
        helper_text: str = "",
        on_change: Optional[Callable[[str], None]] = None,
        **kwargs
    ):
        bg = kwargs.pop("bg", C["bg_root"])
        super().__init__(parent, bg=bg, **kwargs)
        
        # 属性
        self.label_text = label
        self.options = options or []
        self.disabled = disabled
        self.required = required
        self.error_text = error
        self.helper_text = helper_text
        self.on_change = on_change
        
        # 状态
        self._open = False
        self._popup = None
        self._selected = value or (self.options[0] if self.options else "")
        
        # 创建内容
        self._create_content()
    
    def _create_content(self):
        """创建内容"""
        # 标签
        if self.label_text:
            label_frame = tk.Frame(self, bg=self["bg"])
            label_frame.pack(fill=tk.X, pady=(0, S.get("xs")))
            
            self.label = tk.Label(
                label_frame,
                text=self.label_text,
                font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
                fg=C["text_secondary"],
                bg=self["bg"],
                anchor=tk.W,
            )
            self.label.pack(side=tk.LEFT)
            
            if self.required:
                required_label = tk.Label(
                    label_frame,
                    text="*",
                    font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
                    fg=C["accent_danger"],
                    bg=self["bg"],
                )
                required_label.pack(side=tk.LEFT, padx=(2, 0))
        
        # 选择框容器
        select_container = tk.Frame(
            self,
            bg=C["bg_input"],
            highlightthickness=1,
            highlightbackground=C["border_light"],
        )
        select_container.pack(fill=tk.X)
        
        # 选择按钮
        self.select_btn = tk.Label(
            select_container,
            text=self._selected,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_primary"],
            bg=C["bg_input"],
            anchor=tk.W,
            padx=S.get("md"),
            pady=S.get("sm"),
            cursor="hand2",
        )
        self.select_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # 下拉箭头
        arrow_label = tk.Label(
            select_container,
            text="▼",
            font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
            fg=C["text_tertiary"],
            bg=C["bg_input"],
            padx=S.get("md"),
            pady=S.get("sm"),
            cursor="hand2",
        )
        arrow_label.pack(side=tk.RIGHT)
        
        # 绑定事件
        self.select_btn.bind("<Button-1>", lambda e: self._toggle())
        arrow_label.bind("<Button-1>", lambda e: self._toggle())
        
        # 错误/辅助文本
        if self.error_text:
            self._show_error(self.error_text)
        elif self.helper_text:
            self._show_helper(self.helper_text)
    
    def _toggle(self):
        """切换下拉菜单"""
        if self.disabled:
            return
        
        if self._open:
            self._close()
            return
        
        self._open = True
        
        # 计算位置
        x = self.select_btn.winfo_rootx()
        y = self.select_btn.winfo_rooty() + self.select_btn.winfo_height()
        w = self.select_btn.winfo_width()
        
        # 创建弹出窗口
        self._popup = tk.Toplevel(self)
        self._popup.overrideredirect(True)
        self._popup.configure(bg=C["bg_input"])
        
        # 计算高度
        item_height = 32
        max_height = min(len(self.options) * item_height, 200)
        
        self._popup.geometry(f"{w}x{max_height}+{x}+{y}")
        
        # 添加选项
        for option in self.options:
            option_label = tk.Label(
                self._popup,
                text=option,
                font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
                fg=C["text_primary"],
                bg=C["bg_input"],
                anchor=tk.W,
                padx=S.get("md"),
                pady=S.get("xs"),
                cursor="hand2",
            )
            option_label.pack(fill=tk.X)
            
            # 绑定事件
            option_label.bind("<Button-1>", lambda e, val=option: self._select(val))
            option_label.bind("<Enter>", lambda e, l=option_label: l.configure(bg=C["hover_bg"]))
            option_label.bind("<Leave>", lambda e, l=option_label: l.configure(bg=C["bg_input"]))
        
        # 绑定焦点事件
        self._popup.bind("<FocusOut>", lambda e: self._close())
        self._popup.focus_set()
    
    def _select(self, value: str):
        """选择选项"""
        self._selected = value
        self.select_btn.configure(text=value)
        
        if self.on_change:
            self.on_change(value)
        
        self._close()
    
    def _close(self):
        """关闭下拉菜单"""
        self._open = False
        if self._popup:
            self._popup.destroy()
            self._popup = None
    
    def _show_error(self, message: str):
        """显示错误信息"""
        self.error_text = message
        
        for child in self.winfo_children():
            if isinstance(child, tk.Label) and child != getattr(self, 'label', None):
                child.destroy()
        
        error_label = tk.Label(
            self,
            text=message,
            font=(T.get_font_family(), T.get_font_config("caption")["size"]),
            fg=C["accent_danger"],
            bg=self["bg"],
            anchor=tk.W,
        )
        error_label.pack(fill=tk.X, pady=(S.get("xs"), 0))
    
    def _show_helper(self, message: str):
        """显示辅助文本"""
        self.helper_text = message
        
        for child in self.winfo_children():
            if isinstance(child, tk.Label) and child != getattr(self, 'label', None):
                child.destroy()
        
        helper_label = tk.Label(
            self,
            text=message,
            font=(T.get_font_family(), T.get_font_config("caption")["size"]),
            fg=C["text_tertiary"],
            bg=self["bg"],
            anchor=tk.W,
        )
        helper_label.pack(fill=tk.X, pady=(S.get("xs"), 0))
    
    def get_value(self) -> str:
        """获取值"""
        return self._selected
    
    def set_value(self, value: str):
        """设置值"""
        if value in self.options:
            self._selected = value
            self.select_btn.configure(text=value)
    
    def set_options(self, options: List[str]):
        """设置选项"""
        self.options = options
        if self._selected not in options:
            self._selected = options[0] if options else ""
            self.select_btn.configure(text=self._selected)
    
    def set_error(self, message: str):
        """设置错误信息"""
        self._show_error(message)
    
    def clear_error(self):
        """清除错误信息"""
        self.error_text = ""
        
        for child in self.winfo_children():
            if isinstance(child, tk.Label) and child != getattr(self, 'label', None):
                child.destroy()
        
        if self.helper_text:
            self._show_helper(self.helper_text)


class Toggle(tk.Frame):
    """现代化开关组件"""
    
    def __init__(
        self,
        parent,
        label: str = "",
        value: bool = False,
        disabled: bool = False,
        on_change: Optional[Callable[[bool], None]] = None,
        **kwargs
    ):
        bg = kwargs.pop("bg", C["bg_root"])
        super().__init__(parent, bg=bg, **kwargs)
        
        # 属性
        self.label_text = label
        self.value = value
        self.disabled = disabled
        self.on_change = on_change
        
        # 创建内容
        self._create_content()
    
    def _create_content(self):
        """创建内容"""
        # 开关容器
        toggle_container = tk.Frame(self, bg=self["bg"])
        toggle_container.pack(fill=tk.X)
        
        # 开关画布
        self.toggle_canvas = tk.Canvas(
            toggle_container,
            width=44,
            height=24,
            bg=self["bg"],
            highlightthickness=0,
            cursor="hand2" if not self.disabled else "",
        )
        self.toggle_canvas.pack(side=tk.LEFT)
        
        # 绘制开关
        self._draw_toggle()
        
        # 标签
        if self.label_text:
            self.label = tk.Label(
                toggle_container,
                text=self.label_text,
                font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
                fg=C["text_secondary"],
                bg=self["bg"],
                anchor=tk.W,
            )
            self.label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(S.get("md"), 0))
        
        # 绑定事件
        self.toggle_canvas.bind("<Button-1>", self._on_toggle)
    
    def _draw_toggle(self):
        """绘制开关"""
        self.toggle_canvas.delete("all")

        if self.value:
            # 开启状态
            target_bg = C["accent_primary"]
            target_circle_x = 32
        else:
            # 关闭状态
            target_bg = C["text_tertiary"]
            target_circle_x = 12

        if self.disabled:
            target_bg = C["border_light"]

        # 背景
        self.toggle_canvas.create_rounded_rectangle(
            2, 4, 42, 20,
            radius=10,
            fill=target_bg,
            outline="",
        )

        # 圆形滑块
        self.toggle_canvas.create_oval(
            target_circle_x - 8, 6,
            target_circle_x + 8, 22,
            fill=C["text_primary"],
            outline="",
        )
    
    def _on_toggle(self, event):
        """切换开关"""
        if self.disabled:
            return
        
        self.value = not self.value
        self._draw_toggle()
        
        if self.on_change:
            self.on_change(self.value)
    
    def get_value(self) -> bool:
        """获取值"""
        return self.value
    
    def set_value(self, value: bool):
        """设置值"""
        self.value = value
        self._draw_toggle()
    
    def set_disabled(self, disabled: bool):
        """设置禁用状态"""
        self.disabled = disabled
        self.toggle_canvas.configure(cursor="" if disabled else "hand2")
        self._draw_toggle()


# 为Canvas添加圆角矩形方法
def _create_rounded_rectangle(self, x1, y1, x2, y2, radius=10, **kwargs):
    """创建圆角矩形"""
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return self.create_polygon(points, smooth=True, **kwargs)

tk.Canvas.create_rounded_rectangle = _create_rounded_rectangle


if __name__ == "__main__":
    # 测试输入组件
    root = tk.Tk()
    root.title("Input Components Test")
    root.geometry("500x600")
    root.configure(bg=C["bg_root"])
    
    # 创建表单
    form_frame = tk.Frame(root, bg=C["bg_root"])
    form_frame.pack(fill=tk.BOTH, expand=True, padx=S.get("xl"), pady=S.get("xl"))
    
    # 标题
    title = tk.Label(
        form_frame,
        text="表单组件测试",
        font=(T.get_font_family(), T.get_font_config("heading_medium")["size"], "bold"),
        fg=C["text_primary"],
        bg=C["bg_root"],
    )
    title.pack(anchor=tk.W, pady=(0, S.get("lg")))
    
    # 文本输入
    input1 = Input(
        form_frame,
        label="用户名",
        placeholder="请输入用户名",
        required=True,
        helper_text="用户名长度为3-20个字符",
    )
    input1.pack(fill=tk.X, pady=(0, S.get("md")))
    
    # 密码输入
    input2 = Input(
        form_frame,
        label="密码",
        placeholder="请输入密码",
        password=True,
        required=True,
    )
    input2.pack(fill=tk.X, pady=(0, S.get("md")))
    
    # 文本区域
    textarea = TextArea(
        form_frame,
        label="描述",
        placeholder="请输入描述信息",
        rows=3,
    )
    textarea.pack(fill=tk.X, pady=(0, S.get("md")))
    
    # 下拉选择
    select = Select(
        form_frame,
        label="角色",
        options=["管理员", "普通用户", "访客"],
        value="普通用户",
    )
    select.pack(fill=tk.X, pady=(0, S.get("md")))
    
    # 开关
    toggle = Toggle(
        form_frame,
        label="启用通知",
        value=True,
    )
    toggle.pack(fill=tk.X, pady=(0, S.get("md")))
    
    # 提交按钮
    def on_submit():
        print(f"用户名: {input1.get_value()}")
        print(f"密码: {input2.get_value()}")
        print(f"描述: {textarea.get_value()}")
        print(f"角色: {select.get_value()}")
        print(f"通知: {toggle.get_value()}")
    
    submit_btn = tk.Label(
        form_frame,
        text="提交",
        font=(T.get_font_family(), T.get_font_config("button_medium")["size"]),
        fg=C["text_primary"],
        bg=C["accent_primary"],
        cursor="hand2",
        padx=S.get("xl"),
        pady=S.get("sm"),
        relief=tk.FLAT,
    )
    submit_btn.pack(pady=(S.get("lg"), 0))
    submit_btn.bind("<Button-1>", lambda e: on_submit())
    
    root.mainloop()