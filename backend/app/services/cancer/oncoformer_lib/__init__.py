"""Oncoformer 推理库（vendor 自上游 kaiwang13/Oncoformer @15b0d47，Apache-2.0）。

仅保留推理所需文件；torch 相关 import 均在使用处进行，
未安装 torch 的轻量部署可以安全 import 本包而不触发报错。
"""

ONCOFORMER_LICENSE = "Apache-2.0 (c) kaiwang13/Oncoformer upstream"
