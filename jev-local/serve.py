"""Serve JevK5 or SemIf over TypeSafe's /v1/systemone format, for Sensei (gateway/jev.py).

Both use the same prompt and letter readout (JevK5's runtime is built on SemIf's); they differ
in weights and calibration temperature:

    python serve.py --model models/jevk5 --port 8095                       # T from jevk5_config.json (1.532)
    python serve.py --model models/semif --port 8096 --temperature 1.23    # SemIf, authored-decision T
"""
import argparse
from http.server import ThreadingHTTPServer

from jevk5.runtime import JevK5
from jevk5.server import make_handler

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True)
ap.add_argument("--name", help="reported model name (default: the directory name)")
ap.add_argument("--port", type=int, required=True)
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--temperature", type=float, help="calibration T (default: from the model's config, else 1.0)")
args = ap.parse_args()
name = args.name or args.model.rstrip("/").split("/")[-1]
model = JevK5(args.model, temperature=args.temperature)
print(f"{name}: T={model.temperature}, serving on http://{args.host}:{args.port}/v1/systemone", flush=True)
ThreadingHTTPServer((args.host, args.port), make_handler(model, name)).serve_forever()
