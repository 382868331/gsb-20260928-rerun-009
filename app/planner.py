from __future__ import annotations

import math
from collections import deque

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .models import CellPose, Point, Rect, Robot, VerificationResult


def world_to_pixel(point: Point, origin_x: float, origin_y: float, yaw: float, resolution: float, height: int) -> tuple[int, int]:
    c = math.cos(yaw)
    s = math.sin(yaw)
    dx = point.x - origin_x
    dy = point.y - origin_y
    u = c * dx - s * dy
    v = s * dx + c * dy
    return int(round(u / resolution)), height - 1 - int(round(v / resolution))


def rasterize_rect(occupancy: np.ndarray, rect: Rect, origin: tuple[float, float, float], resolution: float) -> None:
    ox, oy, yaw = origin
    h = occupancy.shape[0]
    corners = [
        world_to_pixel(Point(x=rect.x, y=rect.y), ox, oy, yaw, resolution, h),
        world_to_pixel(Point(x=rect.x + rect.width, y=rect.y), ox, oy, yaw, resolution, h),
        world_to_pixel(Point(x=rect.x + rect.width, y=rect.y + rect.height), ox, oy, yaw, resolution, h),
        world_to_pixel(Point(x=rect.x, y=rect.y + rect.height), ox, oy, yaw, resolution, h),
    ]
    layer = Image.new("L", (occupancy.shape[1], h), 0)
    ImageDraw.Draw(layer).polygon(corners, outline=255, fill=255)
    occupancy |= np.asarray(layer, dtype=bool)


def load_occupancy(image, threshold: int = 200) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 3:
        if arr.shape[2] == 4 and np.issubdtype(arr.dtype, np.integer):
            alpha = arr[:, :, 3] < 128
        else:
            alpha = np.zeros(arr.shape[:2], dtype=bool)
        arr = arr[:, :, :3].mean(axis=2)
    else:
        alpha = np.zeros(arr.shape, dtype=bool)
    return (arr < threshold) | alpha


def build_occupancy(image, zones: list[Rect], origin, resolution: float) -> np.ndarray:
    grid = load_occupancy(image)
    for zone in zones:
        rasterize_rect(grid, zone, (origin.x, origin.y, origin.yaw), resolution)
    return grid



def footprint_mask(length: float, width: float, resolution: float, yaw: float) -> np.ndarray:
    extent_cells = math.ceil(math.hypot(length, width) / (2 * resolution)) + 1
    coords = np.arange(-extent_cells, extent_cells + 1)
    xx, yy = np.meshgrid(coords, coords)
    c = math.cos(-yaw)
    s = math.sin(-yaw)
    lx = (xx * c - yy * s) * resolution
    ly = (xx * s + yy * c) * resolution
    samples = np.linspace(-0.45, 0.45, 7)
    hit = np.zeros_like(xx, dtype=bool)
    for sx in samples:
        for sy in samples:
            sample_x = (xx + sx) * resolution
            sample_y = (yy + sy) * resolution
            wx = c * sample_x - s * sample_y
            wy = s * sample_x + c * sample_y
            hit |= (np.abs(wx) <= length / 2 + resolution / 8) & (np.abs(wy) <= width / 2 + resolution / 8)
    return hit



def feasible_poses(occupancy: np.ndarray, robot: Robot, resolution: float, orientations: int) -> np.ndarray:
    free = ~occupancy
    result = np.zeros((orientations, *occupancy.shape), dtype=bool)
    for k in range(orientations):
        yaw = 2 * math.pi * k / orientations
        mask = footprint_mask(robot.length, robot.width, resolution, yaw)
        result[k] = ndimage.binary_erosion(free, structure=mask, border_value=False)
    return result


def feasible_half_poses(occupancy: np.ndarray, robot: Robot, resolution: float, orientations: int) -> np.ndarray:
    free = ~occupancy
    result = np.zeros((orientations, *occupancy.shape), dtype=bool)
    for k in range(orientations):
        yaw = 2 * math.pi * (k + 0.5) / orientations
        mask = footprint_mask(robot.length, robot.width, resolution, yaw)
        result[k] = ndimage.binary_erosion(free, structure=mask, border_value=False)
    return result



def verify_passage(
    occupancy: np.ndarray,
    resolution: float,
    robot: Robot,
    start_world: Point,
    goal_world: Point,
    origin: tuple[float, float, float],
    height: int,
    orientations: int = 24,
) -> VerificationResult:
    from datetime import UTC, datetime

    poses = feasible_poses(occupancy, robot, resolution, orientations)
    half_poses = feasible_half_poses(occupancy, robot, resolution, orientations)
    sx, sy = world_to_pixel(start_world, *origin, resolution, height)
    gx, gy = world_to_pixel(goal_world, *origin, resolution, height)
    h, w = occupancy.shape

    def valid_cell(x: int, y: int) -> bool:
        return 0 <= x < w and 0 <= y < h

    if not valid_cell(sx, sy) or not valid_cell(gx, gy):
        reason = "起点或终点不在地图范围内"
        return VerificationResult(feasible=False, reason=reason, robot_width=robot.width, robot_length=robot.length, start=start_world, goal=goal_world, orientations=orientations, checked_at=datetime.now(UTC).isoformat())

    start_angles = np.flatnonzero(poses[:, sy, sx])
    goal_angles = np.flatnonzero(poses[:, gy, gx])
    if start_angles.size == 0:
        reason = "起点无法容纳完整机器人（含边界和禁行区）"
    elif goal_angles.size == 0:
        reason = "终点无法容纳完整机器人（含边界和禁行区）"
    else:
        reason = ""
    if reason:
        return VerificationResult(feasible=False, reason=reason, robot_width=robot.width, robot_length=robot.length, start=start_world, goal=goal_world, orientations=orientations, checked_at=datetime.now(UTC).isoformat())

    goals = {(int(gx), int(gy), int(a)) for a in goal_angles}
    queue = deque((int(sx), int(sy), int(a)) for a in start_angles)
    parents = {(int(sx), int(sy), int(a)): None for a in start_angles}
    goal_state = None
    directions = ((1, 0), (-1, 0), (0, 1), (0, -1))

    while queue:
        state = queue.popleft()
        x, y, a = state
        if state in goals:
            goal_state = state
            break
        for dx, dy in directions:
            nx, ny = x + dx, y + dy
            if not valid_cell(nx, ny) or not poses[a, ny, nx] or not poses[a, (y + ny) // 2, (x + nx) // 2]:
                continue
            nxt = (nx, ny, a)
            if nxt not in parents:
                parents[nxt] = state
                queue.append(nxt)
        for na in ((a + 1) % orientations, (a - 1) % orientations):
            half_index = a if na == (a + 1) % orientations else na
            if poses[na, y, x] and half_poses[half_index, y, x]:
                nxt = (x, y, na)
                if nxt not in parents:
                    parents[nxt] = state
                    queue.append(nxt)

    checked_at = datetime.now(UTC).isoformat()
    if goal_state is None:
        return VerificationResult(feasible=False, reason="机器人整台足迹无法从起点到达终点；通道对当前尺寸和可旋转范围不足", robot_width=robot.width, robot_length=robot.length, start=start_world, goal=goal_world, orientations=orientations, checked_at=checked_at)

    reversed_path = []
    state = goal_state
    while state is not None:
        reversed_path.append(state)
        state = parents[state]
    reversed_path.reverse()
    stride = max(1, len(reversed_path) // 120)
    selected = reversed_path[::stride]
    if selected[-1] != reversed_path[-1]:
        selected.append(reversed_path[-1])
    path = []
    ox, oy, yaw = origin
    for x, y, a in selected:
        v = (height - 1 - y) * resolution
        wx = ox + math.cos(yaw) * x * resolution + math.sin(yaw) * v
        wy = oy - math.sin(yaw) * x * resolution + math.cos(yaw) * v
        path.append(CellPose(x=wx, y=wy, yaw=2 * math.pi * a / orientations))
    return VerificationResult(feasible=True, reason="存在完整机器人可通行路径", robot_width=robot.width, robot_length=robot.length, start=start_world, goal=goal_world, orientations=orientations, path=path, checked_at=checked_at)
