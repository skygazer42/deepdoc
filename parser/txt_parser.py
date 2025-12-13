#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import re
import os
from pathlib import Path

import tiktoken

from parser.utils import get_text

# 从本地加载 tiktoken 编码文件
_RESOURCE_DIR = Path(__file__).resolve().parent.parent / "resources" / "tiktoken"
_TIKTOKEN_CACHE = os.path.join(_RESOURCE_DIR, "cl100k_base.tiktoken")

# 设置 tiktoken 缓存目录环境变量
if _RESOURCE_DIR.exists():
    os.environ["TIKTOKEN_CACHE_DIR"] = str(_RESOURCE_DIR)

_encoder = None

def _get_encoder():
    global _encoder
    if _encoder is None:
        try:
            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoder = None
    return _encoder


def num_tokens_from_string(string: str) -> int:
    """Returns the number of tokens in a text string."""
    try:
        enc = _get_encoder()
        if enc:
            return len(enc.encode(string))
        return len(string) // 4
    except Exception:
        return len(string) // 4


class RAGFlowTxtParser:
    def __call__(self, fnm, binary=None, chunk_token_num=128, delimiter="\n!?;。；！？"):
        txt = get_text(fnm, binary)
        return self.parser_txt(txt, chunk_token_num, delimiter)

    @classmethod
    def parser_txt(cls, txt, chunk_token_num=128, delimiter="\n!?;。；！？"):
        if not isinstance(txt, str):
            raise TypeError("txt type should be str!")
        cks = [""]
        tk_nums = [0]
        delimiter = delimiter.encode('utf-8').decode('unicode_escape').encode('latin1').decode('utf-8')

        def add_chunk(t):
            nonlocal cks, tk_nums, delimiter
            tnum = num_tokens_from_string(t)
            if tk_nums[-1] > chunk_token_num:
                cks.append(t)
                tk_nums.append(tnum)
            else:
                cks[-1] += t
                tk_nums[-1] += tnum

        dels = []
        s = 0
        for m in re.finditer(r"`([^`]+)`", delimiter, re.I):
            f, t = m.span()
            dels.append(m.group(1))
            dels.extend(list(delimiter[s: f]))
            s = t
        if s < len(delimiter):
            dels.extend(list(delimiter[s:]))
        dels = [re.escape(d) for d in dels if d]
        dels = [d for d in dels if d]
        dels = "|".join(dels)
        secs = re.split(r"(%s)" % dels, txt)
        for sec in secs:
            if re.match(f"^{dels}$", sec):
                continue
            add_chunk(sec)

        return [[c, ""] for c in cks]


if __name__ == "__main__":
    import sys

    # 传入txt路径
    file_path = "/data/Langagent/deepdoc/data/identity.txt"
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    parser = RAGFlowTxtParser()
    # 解析txt文件
    chunks = parser(file_path, chunk_token_num=128)
    print(f"📄 共切分出 {len(chunks)} 个段落：")
    for i, (text, _) in enumerate(chunks):
        print(f"\n=== Chunk {i + 1} ===")
        print(f"内容（前60字）: {text[:60]}...")
        print(f"Token 数量: {num_tokens_from_string(text)}")
