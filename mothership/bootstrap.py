"""本地演示用母舰启动器。生产环境请由部署平台注入管理员令牌。"""
import argparse
import os
import secrets

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 Nan Sentinel 情报母舰")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--token", default="")
    args = parser.parse_args()

    token = args.token or "nsa_" + secrets.token_urlsafe(36)
    os.environ["MOTHERSHIP_ADMIN_TOKEN"] = token
    print("\n情报母舰管理员令牌（本次启动有效，请复制到哨站设置）：", flush=True)
    print(token, flush=True)
    print(f"\n母舰地址：http://{args.host}:{args.port}\n", flush=True)
    uvicorn.run("mothership.app:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
