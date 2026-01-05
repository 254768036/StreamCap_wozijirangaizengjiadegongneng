import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config.config_manager import ConfigManager
from app.core.platforms.platform_handlers import get_platform_info
import streamget


async def check_duplicate_room_ids():
    """检查重复的直播间 room_id"""
    import os
    import json

    # 直接读取正确的配置文件
    config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "recordings.json")
    with open(config_file, "r", encoding="utf-8") as f:
        recordings_data = json.load(f)

    print(f"Total recordings loaded: {len(recordings_data)}")
    print()

    # 构建 URL 到 Recording 的映射
    url_to_rec = {rec["url"]: rec for rec in recordings_data}

    # 按平台分组
    platform_groups = {}
    for rec in recordings_data:
        url = rec["url"]
        platform, platform_key = get_platform_info(url)
        if platform_key:
            if platform_key not in platform_groups:
                platform_groups[platform_key] = []
            platform_groups[platform_key].append(url)

    # 为每个平台的 URL 获取 room_id
    print("Fetching room_id for each URL...")
    print("=" * 80)
    print()

    room_id_map = {}

    for platform_key, urls in platform_groups.items():
        if platform_key == "douyin":
            # 抖音特殊处理
            douyin = streamget.DouyinLiveStream()
            for url in urls:
                try:
                    print(f"Checking: {url_to_rec[url]['streamer_name']}")
                    print(f"  URL: {url}")
                    data = await douyin.fetch_app_stream_data(url)
                    room_id = data.get("id_str", data.get("id"))
                    if room_id:
                        if room_id not in room_id_map:
                            room_id_map[room_id] = []
                        room_id_map[room_id].append(
                            {"name": url_to_rec[url]["streamer_name"], "url": url, "rec_id": url_to_rec[url]["rec_id"]}
                        )
                        print(f"  Room ID: {room_id}")
                    else:
                        print(f"  Room ID: None")
                    print()
                except Exception as e:
                    print(f"  Error: {e}")
                    print()
        else:
            # 其他平台暂时跳过
            for url in urls:
                print(f"Skipping (platform: {platform_key}): {url_to_rec[url]['streamer_name']}")

    print()
    print("=" * 80)
    print("Duplicate Room Detection:")
    print("=" * 80)
    print()

    # 找出重复的 room_id
    duplicates_found = False
    for room_id, items in room_id_map.items():
        if len(items) > 1:
            duplicates_found = True
            print(f"Found duplicate Room ID: {room_id}")
            print(f"  Total {len(items)} recordings:")
            for i, item in enumerate(items, 1):
                print(f"    {i}. {item['name']} (rec_id: {item['rec_id']})")
                print(f"       URL: {item['url']}")
            print()

    if not duplicates_found:
        print("No duplicate rooms found")
    else:
        print("=" * 80)
        print("Recommendations:")
        print("=" * 80)
        print("1. Delete duplicate recording items (keep one)")
        print("2. The system will detect duplicates when adding new recording items")


if __name__ == "__main__":
    asyncio.run(check_duplicate_room_ids())
