"""本地知识库存储：kb.sqlite（v1.7 RAG，仅标准库 sqlite3）。

表结构：
  documents(id, name, source, sha1, char_len, chunk_count, created_at)
  chunks(id, doc_id, seq, title, content)        -- doc_id 级联删除
  kb_fts(FTS5, external-content=chunks, tokenize=trigram)
        -- 触发器自动同步增删改；trigram 使中文无需分词即可子串命中

库路径：rag.kb.dir（config）非空则用其下 kb.sqlite；否则与 config.json 同目录
的 kb\\kb.sqlite（便携模式随 exe 目录走）。本模块不 import tkinter / UI。
"""
import hashlib
import os
import sqlite3
import threading
import time

from .splitter import split_text

# 供 bm25.py 检索使用（模块内约定，勿外部 import 后改动）
_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  source TEXT,
  sha1 TEXT NOT NULL,
  char_len INTEGER DEFAULT 0,
  chunk_count INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  title TEXT DEFAULT '',
  content TEXT NOT NULL,
  vec BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
  content, content='chunks', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO kb_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO kb_fts(kb_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO kb_fts(kb_fts, rowid, content) VALUES('delete', old.id, old.content);
  INSERT INTO kb_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def default_kb_dir(config_path=None):
    """kb 库默认目录：与配置文件同目录（config 放哪 kb 就跟哪）。"""
    from qt.config import Config        # 延迟 import 防循环
    base = os.path.dirname(config_path or Config().path)
    return os.path.join(base, "kb")


class KbStore:
    """进程内单例使用；跨线程访问由内部锁串行化（入库/检索都是低频）。"""

    def __init__(self, cfg=None, db_path=None):
        from qt.config import Config
        self._cfg = cfg or Config()
        if db_path is None:
            custom = (self._cfg.get("rag.kb.dir") or "").strip()
            base = custom if custom else default_kb_dir(self._cfg.path)
            db_path = os.path.join(base, "kb.sqlite")
        self.path = db_path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)   # 首次使用自建目录
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._conn.execute("PRAGMA journal_mode = WAL")
        except Exception:
            pass
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # 旧库迁移：chunks.vec 列（v1.7 向量路新增）
            try:
                self._conn.execute("ALTER TABLE chunks ADD COLUMN vec BLOB")
            except Exception:
                pass            # 列已存在
            self._conn.commit()

    # ------------------------------------------------------------ 向量存取
    def update_vec(self, chunk_id, blob):
        with self._lock:
            self._conn.execute("UPDATE chunks SET vec = ? WHERE id = ?",
                               (blob, chunk_id))
            self._conn.commit()

    def vec_count(self):
        return self._count("SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL")

    def iter_vec_rows(self):
        """全量 (id, vec) 供余弦检索（不取 content，省内存）。"""
        with self._lock:
            return [tuple(r) for r in self._conn.execute(
                "SELECT id, vec FROM chunks WHERE vec IS NOT NULL")]

    def chunk_details(self, ids):
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, doc_id, title, content FROM chunks"
                f" WHERE id IN ({marks})", ids)
            return {r["id"]: {"id": r["id"], "doc_id": r["doc_id"],
                              "title": r["title"], "content": r["content"]}
                    for r in rows}

    # ------------------------------------------------------------ 基础访问
    @property
    def db(self):
        """检索层（bm25.py）只读共用连接；写操作必须经本类方法。"""
        return self._conn

    def close(self):
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def _count(self, sql, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchone()[0]

    def doc_count(self):
        return self._count("SELECT COUNT(*) FROM documents")

    def chunk_count(self):
        return self._count("SELECT COUNT(*) FROM chunks")

    # ------------------------------------------------------------ 入库/删除
    def add_document(self, name, text, source=""):
        """入库一篇文档。内容哈希相同则跳过（返回 added=False）。

        返回 {"added": bool, "doc_id": int|None, "chunks": int, "reason": str}
        """
        text = text or ""
        sha1 = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
        kb = self._cfg.get("rag.kb") or {}
        chunks = split_text(text, name=name,
                            chunk_chars=int(kb.get("chunk_chars", 500) or 500),
                            chunk_overlap=int(kb.get("chunk_overlap", 50) or 50))
        with self._lock:
            old = self._conn.execute(
                "SELECT id FROM documents WHERE sha1 = ?", (sha1,)).fetchone()
            if old:
                return {"added": False, "doc_id": old["id"], "chunks": 0,
                        "reason": "内容与库内已有文档相同，已跳过"}
            if not chunks:
                return {"added": False, "doc_id": None, "chunks": 0,
                        "reason": "文档无有效正文"}
            cur = self._conn.execute(
                "INSERT INTO documents(name, source, sha1, char_len, chunk_count,"
                " created_at) VALUES(?,?,?,?,?,?)",
                (name, source or "", sha1, len(text), len(chunks),
                 time.strftime("%Y-%m-%d %H:%M:%S")))
            doc_id = cur.lastrowid
            self._conn.executemany(
                "INSERT INTO chunks(doc_id, seq, title, content)"
                " VALUES(?,?,?,?)",
                [(doc_id, c["seq"], c["title"], c["content"]) for c in chunks])
            self._conn.commit()
            return {"added": True, "doc_id": doc_id, "chunks": len(chunks),
                    "reason": ""}

    def list_documents(self):
        """文档清单（文档库管理 UI 用）。含每文档已向量化的 chunk 数 vec_n。"""
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT d.id, d.name, d.source, d.char_len, d.chunk_count,"
                "       d.created_at,"
                "       (SELECT COUNT(*) FROM chunks c"
                "         WHERE c.doc_id = d.id AND c.vec IS NOT NULL) AS vec_n"
                "  FROM documents d ORDER BY d.id")]

    def delete_document(self, doc_id):
        """删除单篇文档（级联清 chunks + FTS 触发器清索引）。返回剩余 chunk 数。"""
        with self._lock:
            self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self._conn.commit()
            return self.chunk_count()

    def clear(self):
        """清空整个库（危险操作，调用方负责二次确认）。"""
        with self._lock:
            self._conn.execute("DELETE FROM documents")
            self._conn.execute("DELETE FROM sqlite_sequence WHERE name IN"
                               " ('documents','chunks')")
            self._conn.commit()
