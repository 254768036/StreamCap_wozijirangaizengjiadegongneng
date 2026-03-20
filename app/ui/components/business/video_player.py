import os
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import flet as ft
import flet_video as ftv

from ....utils import utils
from ....utils.logger import logger


class VideoPlayer:
    def __init__(self, app):
        self.app = app
        self._ = {}
        self.load_language()

    def load_language(self):
        language = self.app.language_manager.language
        for key in ("video_player", "storage_page", "base"):
            self._.update(language.get(key, {}))

    async def create_video_dialog(
        self, title: str, video_source: str, is_file_path: bool = True, room_url: str | None = None
    ):
        """
        Create video playback dialog
        :param title: Dialog title
        :param video_source: Video source (file path or URL)
        :param is_file_path: Whether in file path mode
        :param room_url: Live room URL
        """

        def close_dialog(_):
            dialog.open = False
            self.app.dialog_area.update()

        is_mobile = self.app.is_mobile

        if is_mobile:
            video_width = 320
            video_height = 180
        else:
            video_width = 480
            video_height = 270

        video = ftv.Video(
            width=video_width, height=video_height, playlist=[ftv.VideoMedia(video_source)], autoplay=True
        )

        async def copy_source(_):
            self.app.page.set_clipboard(video_source)
            await self.app.snack_bar.show_snack_bar(self._["copy_success"])

        async def open_in_browser(_):
            self.app.page.launch_url(room_url)

        actions = [ft.TextButton(self._["close"], on_click=close_dialog)]

        if room_url:
            actions.insert(0, ft.TextButton(self._["open_live_room_page"], on_click=open_in_browser))
        if not is_file_path:
            if self._["stream_source"] in title:
                actions.insert(0, ft.TextButton(self._["copy_stream_url"], on_click=copy_source))
            else:
                actions.insert(0, ft.TextButton(self._["copy_video_url"], on_click=copy_source))

        if is_mobile:
            actions_row = ft.Row(
                controls=actions,
                spacing=5,
                alignment=ft.MainAxisAlignment.CENTER,
                wrap=True,
            )

            video_container = ft.Container(
                content=video,
                alignment=ft.alignment.center,
                width=video_width,
                height=video_height,
            )

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(title, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1, size=14),
                content=ft.Column(
                    [video_container, actions_row],
                    spacing=5,
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
                actions=[],
                inset_padding=ft.padding.only(left=10, right=10, top=5, bottom=5),
                content_padding=ft.padding.only(left=5, right=5, top=5, bottom=0),
            )
        else:
            drag_area = ft.WindowDragArea(
                content=ft.Container(
                    content=ft.Column(
                        [
                            ft.Container(
                                content=ft.Text(
                                    title,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    max_lines=1,
                                    size=14,
                                    weight=ft.FontWeight.BOLD,
                                ),
                                padding=10,
                                bgcolor=ft.colors.SURFACE_VARIANT,
                            ),
                            ft.Container(
                                content=video,
                                padding=5,
                            ),
                            ft.Container(
                                content=ft.Row(
                                    actions,
                                    spacing=5,
                                    alignment=ft.MainAxisAlignment.END,
                                ),
                                padding=10,
                            ),
                        ],
                        spacing=0,
                        tight=True,
                    ),
                    width=video_width + 30,
                    border_radius=8,
                    bgcolor=ft.colors.SURFACE,
                    shadow=ft.BoxShadow(
                        blur_radius=20,
                        spread_radius=2,
                        color=ft.colors.with_opacity(0.3, ft.colors.BLACK),
                        offset=ft.Offset(0, 4),
                    ),
                ),
            )

            dialog = ft.AlertDialog(
                modal=True,
                content=drag_area,
                content_padding=0,
                inset_padding=0,
                shape=ft.RoundedRectangleBorder(radius=8),
            )
        dialog.open = True
        self.app.dialog_area.content = dialog
        self.app.dialog_area.update()

        is_mobile = self.app.is_mobile

        if is_mobile:
            video_width = 320
            video_height = 180
        else:
            video_width = 800
            video_height = 450

        video = ftv.Video(
            width=video_width, height=video_height, playlist=[ftv.VideoMedia(video_source)], autoplay=True
        )

        async def copy_source(_):
            self.app.page.set_clipboard(video_source)
            await self.app.snack_bar.show_snack_bar(self._["copy_success"])

        async def open_in_browser(_):
            self.app.page.launch_url(room_url)

        actions = [ft.TextButton(self._["close"], on_click=close_dialog)]

        if room_url:
            actions.insert(0, ft.TextButton(self._["open_live_room_page"], on_click=open_in_browser))
        if not is_file_path:
            if self._["stream_source"] in title:
                actions.insert(0, ft.TextButton(self._["copy_stream_url"], on_click=copy_source))
            else:
                actions.insert(0, ft.TextButton(self._["copy_video_url"], on_click=copy_source))

        if is_mobile:
            actions_row = ft.Row(
                controls=actions,
                spacing=5,
                alignment=ft.MainAxisAlignment.CENTER,
                wrap=True,
            )

            video_container = ft.Container(
                content=video,
                alignment=ft.alignment.center,
                width=video_width,
                height=video_height,
            )

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(title, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1, size=14),
                content=ft.Column(
                    [video_container, actions_row],
                    spacing=5,
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
                actions=[],
                inset_padding=ft.padding.only(left=10, right=10, top=5, bottom=5),
                content_padding=ft.padding.only(left=5, right=5, top=5, bottom=0),
            )
        else:
            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(title),
                content=video,
                actions=actions,
                actions_alignment=ft.MainAxisAlignment.END,
            )
        dialog.open = True
        self.app.dialog_area.content = dialog
        self.app.dialog_area.update()

    def _get_external_player_path(self) -> str | None:
        """Get external player path from user config"""
        player_path = self.app.settings.user_config.get("external_player_path", "")
        if player_path and os.path.exists(player_path):
            return player_path

        default_ffplay_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "bin",
            "ffplay.exe",
        )
        if os.path.exists(default_ffplay_path):
            return default_ffplay_path
        return None

    async def preview_video(self, source: str, is_file_path: bool = True, room_url: str | None = None):
        """
        Preview video
        :param source: Video source (file path or URL)
        :param is_file_path: Whether in file path mode
        :param room_url: Live room URL
        """
        external_player = self._get_external_player_path()
        use_external_player = self.app.settings.user_config.get("use_external_player", True)

        if use_external_player and external_player:
            await self.open_with_external_player(external_player, source)
            return

        if is_file_path:
            if not utils.is_valid_video_file(source):
                logger.warning(f"unsupported file type: {Path(source).suffix.lower()}")
                await self.app.snack_bar.show_snack_bar(
                    self._["unsupported_file_type"] + ":" + os.path.basename(source)
                )
                return
            title = os.path.basename(source)
        else:
            parsed = urlparse(source)
            params = parse_qs(parsed.query)
            filename = params.get("filename", [""])[0]
            sub_folder = params.get("subfolder", [""])[0]
            if filename:
                title = self._["previewing"] + ": " + (f"{sub_folder}/{filename}" if sub_folder else filename)
                if Path(filename).suffix.lower() != ".mp4":
                    await self.app.snack_bar.show_snack_bar(self._["unsupported_play_on_web"])
                    return
            else:
                title = self._["view_stream_source_now"]
        await self.create_video_dialog(title, source, is_file_path, room_url)

    async def open_with_external_player(self, player_path: str, source: str):
        """Open video source with external player (e.g., ffplay)"""
        try:
            player_args = self.app.settings.user_config.get("external_player_args", "")

            if is_file_path := os.path.exists(source):
                title = os.path.basename(source)
            else:
                title = "直播预览"

            if player_args:
                args_list = [player_path, "-window_title", title] + player_args.split() + [source]
            else:
                args_list = [player_path, "-window_title", title, source]

            subprocess.Popen(args_list)
            await self.app.snack_bar.show_snack_bar(self._["opened_with_external_player"])
            logger.info(f"Opened {source} with external player: {player_path} with args: {player_args}")
        except Exception as e:
            logger.error(f"Failed to open external player: {e}")
            await self.app.snack_bar.show_snack_bar(self._["failed_to_open_external_player"])
