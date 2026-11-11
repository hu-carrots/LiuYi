# -*- coding: utf-8 -*-
"""天气(免 Key): 中国天气网优先, open-meteo 兜底。

本模块只用标准库(urllib), 不 import PyQt5。联网在调用方开的后台线程里做,
不要在 UI 线程直接调 fetch_weather()。

链路(实测于 2026-11-11, 本机为 Linux VM, 中国天气网在该环境下有 403 风险,
Windows 真机上以实际为准; 每一步失败都落到下一步, 最后才报错):
    1. 城市代码: toy1.weather.com.cn/search 搜 -> 内置常用城市表 -> open-meteo 地理编码
    2. 实况: d1.weather.com.cn/sk_2d/{code}.html (需带 Referer, 返回 var dataSK={...})
    3. 兜底: open-meteo forecast current (纬度/经度来自地理编码)
"""

import json
import urllib.parse
import urllib.request

from .phase1 import (
    COMMON_CITY_CODES, normalize_city_name, parse_city_search,
    parse_openmeteo_current, parse_sk_2d,
)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REFERER = "http://www.weather.com.cn/"
TIMEOUT = 8


class WeatherError(Exception):
    pass


def _http_get(url: str, timeout: int = TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except Exception as exc:
        raise WeatherError(f"请求失败: {exc}") from exc


def _city_code_by_search(city: str):
    """toy1 搜索城市代码; 搜不到/返回空返回 None。"""
    query = urllib.parse.quote(city)
    text = _http_get(f"http://toy1.weather.com.cn/search?cityname={query}")
    results = parse_city_search(text)
    if not results:
        return None
    # 优先精确匹配, 否则取第一个
    for name, code in results:
        if normalize_city_name(name) == city:
            return code
    return results[0][1]


def _city_geo(city: str):
    """open-meteo 地理编码 -> (纬度, 经度, 显示名); 找不到返回 None。"""
    query = urllib.parse.quote(city)
    text = _http_get(
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={query}&count=5&language=zh&format=json")
    try:
        data = json.loads(text)
    except ValueError:
        return None
    for item in data.get("results") or []:
        if "latitude" in item and "longitude" in item:
            return float(item["latitude"]), float(item["longitude"]), str(item.get("name", city))
    return None


def _fetch_sk_2d(code: str) -> dict:
    text = _http_get(f"http://d1.weather.com.cn/sk_2d/{code}.html")
    try:
        return parse_sk_2d(text)
    except ValueError as exc:
        raise WeatherError(str(exc)) from exc


def _fetch_openmeteo(lat: float, lon: float) -> dict:
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}"
           "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
           "&timezone=Asia%2FShanghai")
    text = _http_get(url)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise WeatherError("open-meteo 返回解析失败") from exc
    if data.get("error"):
        raise WeatherError("open-meteo 返回错误: " + str(data.get("reason", "")))
    try:
        return parse_openmeteo_current(data)
    except ValueError as exc:
        raise WeatherError(str(exc)) from exc


def fetch_weather(city: str, timeout: int = TIMEOUT) -> dict:
    """查一个城市的实况天气。

    返回 {"city", "temp", "weather", "wind", "wind_level", "humidity", "time"}。
    全部链路失败抛 WeatherError(中文原因)。
    """
    city = normalize_city_name(city)
    if not city:
        raise WeatherError("请先在设置里填一个城市")
    errors = []
    # 1) 中国天气网
    code = COMMON_CITY_CODES.get(city)
    if code is None:
        try:
            code = _city_code_by_search(city)
        except WeatherError as exc:
            errors.append(str(exc))
            code = None
    if code:
        try:
            info = _fetch_sk_2d(code)
            info["city"] = info.get("city") or city
            return info
        except WeatherError as exc:
            errors.append(str(exc))
    # 2) open-meteo 兜底(地理编码 -> forecast)
    try:
        geo = _city_geo(city)
        if geo is None:
            raise WeatherError("找不到这个城市")
        lat, lon, _name = geo
        info = _fetch_openmeteo(lat, lon)
        info["city"] = city
        return info
    except WeatherError as exc:
        errors.append(str(exc))
    raise WeatherError("天气查不到: " + "；".join(errors) if errors else "天气查不到")
