import json
import os
import shutil
from datetime import datetime

# 配置文件路径
config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
recordings_file = os.path.join(config_dir, "recordings.json")
backup_file = os.path.join(config_dir, f"recordings_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")

# 重复的 room_id 和需要删除的 rec_id
duplicate_room_id = "7591864559162460955"
rec_id_to_delete = "c0bfee9e-0058-4e86-9c83-465dfbe858e6"  # "我是小鱼"
rec_id_to_keep = "664b214c-1720-468f-b651-65768d916342"  # "小鱼罐头"

print("=" * 80)
print("Remove Duplicate Recording Script")
print("=" * 80)
print()

# 1. 备份原文件
print(f"1. Backing up original file to: {backup_file}")
shutil.copy2(recordings_file, backup_file)
print("   Backup completed")
print()

# 2. 读取配置文件
print(f"2. Reading recordings from: {recordings_file}")
with open(recordings_file, "r", encoding="utf-8") as f:
    recordings_data = json.load(f)

print(f"   Total recordings: {len(recordings_data)}")
print()

# 3. 查找要删除的监控项
print("3. Finding duplicate recording items:")
items_to_delete = []
items_to_keep = []

for rec in recordings_data:
    if rec.get("rec_id") == rec_id_to_delete:
        items_to_delete.append(rec)
        print(f"   [DELETE] {rec['streamer_name']} (rec_id: {rec['rec_id']})")
        print(f"           URL: {rec['url']}")
    elif rec.get("rec_id") == rec_id_to_keep:
        items_to_keep.append(rec)
        print(f"   [KEEP]   {rec['streamer_name']} (rec_id: {rec['rec_id']})")
        print(f"           URL: {rec['url']}")

print()

if not items_to_delete:
    print("No duplicate items found to delete")
    exit(0)

# 4. 删除重复的监控项
print(f"4. Removing {len(items_to_delete)} duplicate recording(s)...")
recordings_data = [rec for rec in recordings_data if rec.get("rec_id") != rec_id_to_delete]
print("   Removal completed")
print()

# 5. 保存更新后的配置
print("5. Saving updated configuration...")
with open(recordings_file, "w", encoding="utf-8") as f:
    json.dump(recordings_data, f, ensure_ascii=False, indent=4)
print(f"   Saved {len(recordings_data)} recordings")
print()

print("=" * 80)
print("Summary")
print("=" * 80)
print(f"✓ Original file backed up to: {backup_file}")
print(f"✓ Deleted {len(items_to_delete)} duplicate recording(s)")
print(f"✓ Kept {len(items_to_keep)} recording(s)")
print(f"✓ Total recordings after cleanup: {len(recordings_data)}")
print()
print("Please restart the StreamCap application to see the changes")
print()
