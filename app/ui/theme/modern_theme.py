import flet as ft

class ColorPalette:
    # Dark Mode Colors
    BACKGROUND = "#1a1b26"  # Deep blue-black
    SURFACE = "#24283b"     # Slightly lighter blue-black
    ACCENT = "#7aa2f7"      # Bright blue
    SECONDARY = "#bb9af7"   # Purple
    SUCCESS = "#9ece6a"     # Green
    WARNING = "#e0af68"     # Orange
    ERROR = "#f7768e"       # Red
    TEXT_PRIMARY = "#c0caf5"
    TEXT_SECONDARY = "#a9b1d6"
    
    # Gradients
    PRIMARY_GRADIENT = [ft.colors.BLUE_600, ft.colors.PURPLE_600]
    GLASS_BG = ft.colors.with_opacity(0.1, ft.colors.WHITE)
    GLASS_BORDER = ft.colors.with_opacity(0.2, ft.colors.WHITE)

class GlassContainer(ft.Container):
    def __init__(
        self,
        content: ft.Control = None,
        width: int | float = None,
        height: int | float = None,
        padding: int | float = 20,
        margin: int | float = 0,
        alignment: ft.Alignment = None,
        on_click=None,
        expand=False,
    ):
        super().__init__(
            content=content,
            width=width,
            height=height,
            padding=padding,
            margin=margin,
            alignment=alignment,
            on_click=on_click,
            expand=expand,
            border_radius=20,
            bgcolor=ft.colors.with_opacity(0.05, ft.colors.WHITE),
            border=ft.border.all(1, ft.colors.with_opacity(0.1, ft.colors.WHITE)),
            blur=ft.Blur(10, 10, ft.BlurTileMode.MIRROR),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=20,
                color=ft.colors.with_opacity(0.1, ft.colors.BLACK),
                offset=ft.Offset(0, 10),
            ),
        )

class ModernButton(ft.Container):
    def __init__(
        self,
        text: str,
        icon: str = None,
        on_click=None,
        width: int = 180,
        height: int = 50,
        gradient_colors: list = None,
    ):
        self.on_click_callback = on_click
        self.gradient_colors = gradient_colors or ColorPalette.PRIMARY_GRADIENT
        
        content_controls = []
        if icon:
            content_controls.append(ft.Icon(icon, color=ft.colors.WHITE, size=20))
        
        content_controls.append(
            ft.Text(
                text,
                color=ft.colors.WHITE,
                size=14,
                weight=ft.FontWeight.W_600,
            )
        )
        
        super().__init__(
            content=ft.Row(
                controls=content_controls,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=10,
            ),
            width=width,
            height=height,
            border_radius=15,
            gradient=ft.LinearGradient(
                begin=ft.alignment.top_left,
                end=ft.alignment.bottom_right,
                colors=self.gradient_colors,
            ),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=15,
                color=ft.colors.with_opacity(0.3, self.gradient_colors[0]),
                offset=ft.Offset(0, 5),
            ),
            on_click=self._on_click,
            on_hover=self._on_hover,
            animate=ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT),
            padding=ft.padding.symmetric(horizontal=20),
        )

    async def _on_click(self, e):
        if self.on_click_callback:
            await self.on_click_callback(e)

    async def _on_hover(self, e):
        if e.data == "true":
            self.scale = 1.05
            self.shadow.blur_radius = 25
            self.shadow.offset = ft.Offset(0, 8)
        else:
            self.scale = 1.0
            self.shadow.blur_radius = 15
            self.shadow.offset = ft.Offset(0, 5)
        self.update()
