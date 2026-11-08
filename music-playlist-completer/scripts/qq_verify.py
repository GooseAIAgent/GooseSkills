#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 QQ 音乐登录态与会员权限，并试取测试曲验证能否拿到 FLAC。

用法：
  python3 qq_verify.py                              # 读配置文件
  python3 qq_verify.py "uin=o123; qm_keyst=Q_H_L_x" # 临时粘贴片段（合并到配置之上）

注意：qm_keyst / uin 是 HttpOnly cookie，浏览器 document.cookie 读不到；
需要在 DevTools Network 面板从请求头复制完整 Cookie，或从 Application→Cookies 取。
"""
import argparse, json, sys, os, re
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcommon import UA, load_config, add_common_args
from qq_dl import qq_cookie, qq_uin, post, vkey


def main():
    ap = argparse.ArgumentParser()
    add_common_args(ap)
    ap.add_argument("fragment", nargs="?", default="",
                    help='可选：临时 cookie 片段，如 "uin=o123; qm_keyst=Q_H_L_xxx"')
    a = ap.parse_args()
    cfg, cfg_path = load_config(a.config)
    ck = qq_cookie(cfg)
    if a.fragment:
        frag = a.fragment.strip().strip("'\"")
        ck = (ck + "; " + frag) if ck else frag
    if not ck:
        print(f"未配置登录态：请在 {cfg_path} 填 qq_uin + qq_keyst（或完整 qq_cookie）。")
        return 1
    uin = qq_uin(ck)
    print("uin:", uin, "| cookie 长度:", len(ck),
          "| 有 qm_keyst:", bool(re.search(r"qm_keyst=|qqmusic_key=", ck)))
    try:
        d = post({"req": {"module": "music.UserInfo.userInfoServer", "method": "GetLoginUserInfo",
                          "param": {}}}, ck)
        info = (d.get("req") or {}).get("data") or {}
        v = info.get("vipInfo") or {}
        print("昵称:", info.get("nick") or info.get("nickname"),
              "| VIP 等级:", v.get("vipType") or v.get("iVipType"))
    except Exception as e:
        print("会员信息读取失败:", e)

    ok_flac = False
    for q, tagname in (("周杰伦 晴天", ""), ("周杰伦 最伟大的作品", " [VIP曲]")):
        try:
            d = post({"req": {"method": "DoSearchForQQMusicDesktop",
                              "module": "music.search.SearchCgiService",
                              "param": {"search_type": 0, "query": q, "page_num": 1,
                                        "num_per_page": 1}}}, ck)
            s = d["req"]["data"]["body"]["song"]["list"][0]
        except Exception as e:
            print("搜索失败:", e); continue
        mid = s["mid"]
        print(f"测试曲{tagname}: {s['name']} | mid: {mid}")
        for code, ext in (("F000", "flac"), ("M800", "mp3")):
            u = vkey(mid, code, ext, ck, uin)
            print(f"  {code} ({ext}): {'OK' if u else '无地址'}")
            if u:
                if code == "F000": ok_flac = True
                r = urllib.request.urlopen(urllib.request.Request(u, headers={
                    "User-Agent": UA, "Referer": "https://y.qq.com/", "Cookie": ck}), timeout=30)
                print("    实际下载:", r.status, r.headers.get("Content-Type"),
                      "magic=", r.read(4), "size=", r.headers.get("Content-Length"))
                r.close()
                break
    if not ok_flac:
        print("\n提示：F000 无损取不到，常见原因——")
        print("  1) 账号无绿钻/豪华绿钻权益；2) 该曲目属付费数字专辑（按版权方分级）；")
        print("  3) vkey 请求未带真实 uin（脚本已自动处理）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
