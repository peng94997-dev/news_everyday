#!/usr/bin/env python3
"""東京・首都圏の日本語ニュース（社交イベント／移民・外国人政策）日報を作る一体型スクリプト。

処理の流れ:
    1. 収集   Google ニュース RSS から当日の記事を取得
    2. 精選   キーワード規則で地域・カテゴリに振り分け
    3. 付加   見出しに ふりがな（pykakasi）と中国語訳を付与
    4. 出力   Markdown / JSON を保存し、閲覧ページ news/index.html を再生成

使い方:
    python3 scripts/fetch_news.py              # 収集から出力まで一括実行
    python3 scripts/fetch_news.py --page-only  # 収集せず、既存データからページだけ再生成
    python3 scripts/fetch_news.py --out DIR    # 出力先のルートを指定（既定: news/）
    python3 scripts/fetch_news.py --serve      # ページを生成してローカルサーバーを起動（単語帳の保存に必要）

出力:
    news/YYYY-MM-DD/tokyo_news_YYYY-MM-DD.md    精選済みニュース（Markdown）
    news/YYYY-MM-DD/tokyo_news_YYYY-MM-DD.json  精選済みニュース（JSON）
    news/YYYY-MM-DD/raw_candidates.json         取得した全候補
    news/translations_zh.json                   中国語訳キャッシュ
    news/explanations.json                      見出しの構造解説キャッシュ（claude CLI で生成）
    news/index.html                             閲覧用ページ（見出しの語を選択して単語帳に追加）
    news/vocab.html                             単語帳の暗記ページ
    user/vocab.md                               単語帳（Markdown、手で編集しても可。個人データ・git 対象外）
    data/jlpt_vocab.json                        JLPT 語彙辞書（初回に自動ダウンロード）

単語帳: 収集のたびに見出し中の N5・N4 単語を自動追加する。各単語には JLPT 等級を付ける。
JLPT 辞書の出典: 5mdld/anki-jlpt-decks（CC BY-NC 4.0）https://github.com/5mdld/anki-jlpt-decks

依存: pykakasi・janome（pip install pykakasi janome）。中国語訳は `claude` CLI（ログイン済みの場合）→ MyMemory 無料 API の順に使う。
"""
import argparse, csv, html, io, json, re, subprocess, sys, threading, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lesson import (LESSON_TEMPLATE, PROGRESS_NAME, USER_DIR, generate_today, lesson_view, load_progress,
                    grammar_marks, load_syllabus, mark_done, migrate_user_data, next_review, parse_lesson_json,
                    review_point, save_progress)

# ==========================================================================
# 1. 収集・精選
# ==========================================================================

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent

# ---- 検索クエリ ----------------------------------------------------------
QUERIES = {
    "imm": [
        "外国人政策", "外国人 在留", "入管", "出入国在留管理庁", "移民 日本",
        "育成就労", "特定技能", "技能実習", "難民 認定", "永住許可",
        "外国人 東京", "外国人 埼玉", "外国人 神奈川", "外国人 千葉", "多文化共生",
        "クルド人 川口", "外国人 土地取得", "不法滞在",
    ],
    "evt": [
        "東京 イベント", "東京都 開催", "都内 祭り", "東京 フェス", "横浜 イベント",
        "埼玉 イベント", "千葉 イベント", "東京 交流会", "東京 マルシェ", "東京 イルミネーション",
        "川崎 イベント", "東京 酉の市", "東京 展示会 開幕",
    ],
}

# ---- 判定用キーワード ----------------------------------------------------
TOKYO = ["東京", "都内", "都庁", "都知事", "小池", "新宿", "渋谷", "池袋", "銀座", "浅草", "品川",
         "上野", "六本木", "お台場", "豊洲", "丸の内", "有楽町", "虎ノ門", "原宿", "表参道", "秋葉原",
         "両国", "麻布", "歌舞伎町", "二子玉川", "吉祥寺", "西荻", "京橋", "日本橋", "羽田",
         "八王子", "立川", "町田", "武蔵野", "江戸川", "足立", "葛飾", "練馬", "世田谷", "大田",
         "江東", "墨田", "台東", "豊島", "板橋", "荒川区", "目黒", "中野", "杉並", "港区", "中央区",
         "千代田", "文京", "府中", "調布", "三鷹", "国分寺", "多摩", "皇居", "東京ドーム", "スカイツリー"]
AROUND = ["神奈川", "横浜", "川崎", "相模原", "鎌倉", "藤沢", "横須賀", "小田原", "みなとみらい", "赤レンガ",
          "大さん橋", "溝口", "海老名", "厚木", "相鉄",
          "埼玉", "さいたま", "川口", "蕨", "所沢", "川越", "越谷", "草加", "八潮", "春日部", "熊谷",
          "千葉", "船橋", "柏", "松戸", "市川", "浦安", "幕張", "成田", "木更津", "印西", "習志野", "舞浜",
          "首都圏", "関東"]
# 「千葉」などを含む人名・チーム名等の誤検出除外
REGION_NG = ["千葉百音", "千葉雄大", "千葉恵里", "千葉ジェッツ", "千葉ロッテ", "川崎希", "柏木"]

# 首都圏の記事に限り移民・共生関連とみなす語（例：神奈川県とベトナムの交流）
IMM_LOCAL_KW = ["ベトナム", "共生", "国際交流", "外国籍"]
IMM_KW = ["外国人", "移民", "入管", "在留", "永住", "育成就労", "特定技能", "技能実習", "難民",
          "不法滞在", "多文化共生", "土地取得", "クルド", "国籍", "外国籍", "ビザ"]
EVT_KW = ["イベント", "祭", "まつり", "フェス", "マルシェ", "開催", "開幕", "展", "イルミ", "交流会",
          "ライブ", "パレード", "フェスタ", "ラリー", "レセプション", "マーケット", "セミナー", "体験"]

# 首都圏以外の地域・海外（国レベルの政策記事からローカル記事を除く）
OTHER_AREA = ["北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島", "新潟", "富山", "石川", "福井",
              "山梨", "長野", "松本", "岐阜", "静岡", "浜松", "愛知", "名古屋", "三重", "滋賀", "京都",
              "大阪", "兵庫", "神戸", "奈良", "和歌山", "鳥取", "島根", "岡山", "広島", "呉", "山口",
              "下関", "徳島", "香川", "愛媛", "松山", "高知", "福岡", "佐賀", "長崎", "熊本", "八代",
              "大分", "宮崎", "鹿児島", "沖縄", "八戸", "はちのへ", "美作", "総社", "茨城", "栃木", "群馬", "つくば",
              "カナダ", "アメリカ", "米国", "トランプ", "スペイン", "ブラジル", "タイ", "ホーチミン",
              "フランス", "DHS", "ハワイ", "台湾", "ミャンマー・ユニティ"]
# 宣伝色の強い媒体・無関係な話題
NG_SOURCES = ["PR TIMES", "note", "Infoseek", "Vinetur", "Mezha.net", "選挙ドットコム", "stib.jp"]
NG_WORDS = ["のイメージ", "画像ギャラリー", "レッツエンジョイ東京", "公式サイト", "魅了", "訪日",
            "インバウンド", "殺人", "横浜FC", "キャンプ", "プロ野球", "B.LEAGUE", "ルヴァン", "試合", "株", "逮捕", "初公判", "不審者", "総裁選",
            "クリスマスケーキ", "ゴールデンウィーク", "ハロウィン", "大阪で開幕", "福岡", "群馬"]

SECTIONS = [
    ("imm_local", "一、移民・外国人政策（東京・首都圏）", 15),
    ("imm_national", "二、移民・外国人政策（国の政策・全国動向）", 15),
    ("evt_tokyo", "三、社交イベント（東京都内）", 25),
    ("evt_around", "四、社交イベント（神奈川・埼玉・千葉）", 15),
]


def fetch(q):
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:1d")
           + "&hl=ja&gl=JP&ceid=JP:ja")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
    for it in root.iter("item"):
        src = it.find("source")
        yield {"title": it.findtext("title") or "", "link": it.findtext("link") or "",
               "pub": parsedate_to_datetime(it.findtext("pubDate")).astimezone(JST),
               "source": src.text if src is not None else ""}


def clean_title(t, source):
    t = t.rsplit(" - ", 1)[0].strip() if " - " in t else t
    t = re.sub(r"^(画像\s*\|\s*|[＜<]?画像\s*\d+\s*/\s*\d+[＞>]\s*|写真\d+/\d+｜)", "", t)
    t = re.sub(r"\s*[–-]\s*Quebee.*$", "", t)
    t = re.sub(r"\s*\(\d{4}年\d+月\d+日掲載\)$", "", t)
    return t.strip()


def norm(t):
    t = re.sub(r"[（(][^（）()]*[)）]$", "", t)  # 末尾の（媒体名）
    return re.sub(r"[\s　「」『』【】\[\]！!？?、。・…]", "", t)


def has(text, words):
    return any(w in text for w in words)


def region_of(title):
    t = title
    for ng in REGION_NG:
        t = t.replace(ng, "")
    if has(t, TOKYO):
        return "tokyo"
    if has(t, AROUND):
        return "around"
    return None


def classify(x):
    """記事をセクションに振り分ける。対象外は None。"""
    t, cat = x["title"], x["category"]
    if has(x["source"], NG_SOURCES) or has(t, NG_WORDS):
        return None
    region = region_of(t)
    if cat == "imm" and (has(t, IMM_KW) or (region and has(t, IMM_LOCAL_KW))):
        if region:
            return "imm_local"
        if not has(t + x["source"], OTHER_AREA):
            return "imm_national"
        return None
    if has(t, EVT_KW) and region:
        return "evt_tokyo" if region == "tokyo" else "evt_around"
    return None


def collect(since):
    seen, out = set(), []
    for cat, qs in QUERIES.items():
        for q in qs:
            try:
                items = list(fetch(q))
            except Exception as e:
                print(f"取得失敗: {q}: {e}", file=sys.stderr)
                continue
            for i in items:
                title = clean_title(i["title"], i["source"])
                key = norm(title)[:25]
                if i["pub"] < since or key in seen:
                    continue
                seen.add(key)
                out.append({"category": cat, "query": q, "title": title, "source": i["source"],
                            "published": i["pub"].strftime("%Y-%m-%d %H:%M"), "link": i["link"],
                            "gallery": bool(GALLERY_RE.match(i["title"]))})
    return out


# ---- 古い記事の除外 ------------------------------------------------------
# Google ニュース RSS の pubDate は「Google が再インデックスした時刻」で、元記事の公開日ではない。
# 何年も前の記事や画像ページが「今日」の日付で流れてくるので、見出しの手がかりで除外する。
GALLERY_RE = re.compile(r"^(画像\s*\||[＜<]?画像\s*\d+\s*/\s*\d+|写真\d+/\d+)")
SEASON_WORDS = {"ゴールデンウィーク": (4, 5), "GW": (4, 5), "シルバーウィーク": (9, 9), "お花見": (3, 4),
                "花見": (3, 4), "夏休み": (7, 8), "お盆": (8, 8), "夏祭り": (7, 8), "花火大会": (7, 8)}


def seen_titles(news_dir, before):
    """before より前の日に収録した見出しのキー集合（再インデックスされた同じ記事の検出用）。"""
    keys = set()
    for f in news_dir.glob("*/tokyo_news_*.json"):
        if f.parent.name < before:
            keys |= {norm(x["title"])[:25] for x in json.loads(f.read_text())}
    return keys


def stale_reason(x, today, seen=frozenset()):
    """古い記事と判断した理由を返す（新しければ None）。"""
    t = x["title"]
    if x.get("gallery"):
        return "图集页面（多为旧文章被重新收录）"
    if norm(t)[:25] in seen:
        return "之前的日期已收录过"
    years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", t)]
    if years and max(years) < today.year:
        return f"标题中的年份 {max(years)} 早于今年"
    if years and max(years) > today.year:
        return None  # 来年以降の予定（「2027年の…5月15日」など）
    for m, d in re.findall(r"(?<![\d年])(\d{1,2})月(\d{1,2})日", t):
        try:
            dt = datetime(today.year, int(m), int(d)).date()
        except ValueError:
            continue
        # 今年の日付として 45 日以上前、かつ 6 か月以内の過去なら古い記事（それより前なら来年の予定とみなす）
        if timedelta(days=45) < today - dt < timedelta(days=183):
            return f"标题中的日期 {m}月{d}日 已过去较久"
    for w, (m1, m2) in SEASON_WORDS.items():
        if w in t and not (m1 - 1 <= today.month <= m2):
            return f"季节词「{w}」与当前月份不符"
    return None


def render(picked, now, since):
    L = [f"# 東京・首都圏ニュース（社交イベント／移民政策）— {now:%Y年%m月%d日}", "",
         f"- 収集日時：{now:%Y-%m-%d %H:%M} JST",
         f"- 対象期間：{since:%Y-%m-%d %H:%M} 〜 {now:%Y-%m-%d %H:%M} JST",
         "- 出典：Google ニュース（日本語版）RSS 検索、キーワード規則で地域・カテゴリを自動精選",
         "- 注意：時刻は Google の収録時刻。見出しの年・日付・季節語、過去日の重複、画像ページで古い記事を除外済み",
         "- 未精選の全候補は `raw_candidates.json` を参照", ""]
    for key, heading, _ in SECTIONS:
        items = [p for p in picked if p["section"] == key]
        L += [f"## {heading}（{len(items)}件）", ""]
        if not items:
            L += ["（該当なし）", ""]
            continue
        for n, x in enumerate(items, 1):
            L.append(f"{n}. **{x['title']}**  ")
            L.append(f"   {x['source']} ｜ {x['published']} ｜ [記事リンク]({x['link']})")
            if x.get("zh"):
                L.append(f"   中文：{x['zh']}")
        L.append("")
    return "\n".join(L)


# ==========================================================================
# 2. ふりがな・中国語訳
# ==========================================================================

KANJI = re.compile(r"[㐀-鿿々〆ヶ]")
# pykakasi の読み誤りを補正
READING_FIX = {"日本": "にほん", "日本人": "にほんじん", "今日": "きょう", "明日": "あした", "一日": "いちにち"}

_kks = None


def _kakasi():
    global _kks
    if _kks is None:
        import pykakasi
        _kks = pykakasi.kakasi()
    return _kks


def _split_okurigana(orig, hira):
    """「損ね/そこね」→ [["損","そこ"],["ね",""]] のように前後の仮名を分離する。"""
    def kana(c):
        return chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c
    pre = 0
    while pre < len(orig) and pre < len(hira) and not KANJI.match(orig[pre]) and kana(orig[pre]) == hira[pre]:
        pre += 1
    suf = 0
    while (suf < len(orig) - pre and suf < len(hira) - pre and not KANJI.match(orig[-1 - suf])
           and kana(orig[-1 - suf]) == hira[-1 - suf]):
        suf += 1
    segs = []
    if pre:
        segs.append([orig[:pre], ""])
    segs.append([orig[pre:len(orig) - suf], hira[pre:len(hira) - suf]])
    if suf:
        segs.append([orig[len(orig) - suf:], ""])
    return segs


def furigana(text):
    """[[表記, 読み], ...]。読みは漢字を含む区切りのみ。janome で文脈に沿った読みを付ける
    （pykakasi は半角記号などを正規化して元の文字列と一致しなくなるため使わない）。"""
    try:
        toks = list(_tokenizer().tokenize(text))
    except ImportError:
        return [[text, ""]]
    segs, prev = [], ""
    for t in toks:
        orig = t.surface
        hira = READING_FIX.get(orig, _hira(t.reading) if t.reading != "*" else "")
        num = int(prev) if prev.isdigit() else None
        if orig == "月" and num is not None:
            hira = MONTH_READ.get(num, "がつ")
        elif orig == "日" and num is not None:
            hira = DAY_READ.get(num, "にち")
        prev = orig
        if KANJI.search(orig) and hira and hira != orig:
            segs += _split_okurigana(orig, hira)
        elif segs and not segs[-1][1]:
            segs[-1][0] += orig
        else:
            segs.append([orig, ""])
    return segs if "".join(s[0] for s in segs) == text else [[text, ""]]


def _via_claude_cli(titles):
    prompt = ("次の日本語ニュース見出しを中国語（簡体字）に自然に翻訳してください。"
              "入力と同じ順の JSON 文字列配列だけを出力し、説明やコードブロックは付けないこと。\n"
              + json.dumps(titles, ensure_ascii=False))
    r = subprocess.run(["claude", "-p", "--model", "haiku", prompt],
                       capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError((r.stdout + r.stderr).strip()[:200])
    out = parse_lesson_json(r.stdout, array=True)  # 未エスケープの " を直してから読む
    if len(out) != len(titles):
        raise RuntimeError("件数が一致しません")
    return dict(zip(titles, out))


def _via_mymemory(titles):
    res = {}
    for t in titles:
        url = ("https://api.mymemory.translated.net/get?langpair=ja|zh-CN&q="
               + urllib.parse.quote(t[:450]))
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read())
        if data.get("responseStatus") == 200:
            res[t] = html.unescape(re.sub(r"<[^>]+>", "", data["responseData"]["translatedText"])).strip()
    return res


_cli_ok = True  # claude CLI が一度失敗したら以後は試さない（サーバー稼働中の待ち時間対策）


def translate(titles, cache_path):
    global _cli_ok
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = [t for t in dict.fromkeys(titles) if t not in cache]
    if todo:
        print(f"正在翻译 {len(todo)} 条标题…", file=sys.stderr, flush=True)
        for name, fn in (("claude CLI", _via_claude_cli), ("MyMemory", _via_mymemory)):
            if fn is _via_claude_cli and not _cli_ok:
                continue
            try:
                got = fn(todo)
                cache.update(got)
                print(f"翻訳 {len(got)} 件（{name}）", file=sys.stderr)
                break
            except Exception as e:
                print(f"翻訳失敗（{name}）: {e}", file=sys.stderr)
                if fn is _via_claude_cli:
                    _cli_ok = False
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    return cache


# ---- 逐語解析（janome）---------------------------------------------------
TOKENS_VERSION = 3  # 解析ロジックを変えたら上げる（既存データを再解析）
POS_ZH = {"名詞": "名词", "動詞": "动词", "形容詞": "形容词", "副詞": "副词", "連体詞": "连体词",
          "接続詞": "连词", "助詞": "助词", "助動詞": "助动词", "接頭詞": "前缀", "記号": "符号",
          "感動詞": "感叹词", "フィラー": "语气词"}
PROPER_ZH = {"人名": "人名", "地域": "地名", "組織": "机构名", "国": "国名"}
# 助詞・助動詞の働き（基本形 → 説明）
GRAMMAR = {
    "が": "主语标记：前面的词是动作的主体", "は": "主题标记：“说到…”", "を": "宾语标记：动作的对象",
    "に": "表示地点、时间、对象（在／于／给／向）", "で": "表示场所或手段（在…／用…）", "へ": "表示方向（往…）",
    "と": "和、与；或引用（说…）", "の": "的（连接名词）", "から": "从…（起点）", "まで": "到…为止",
    "や": "和（列举部分）", "も": "也；甚至", "より": "比…；从…", "か": "疑问；或者",
    "など": "等等", "ば": "如果…就…", "て": "连接动作：…然后…", "ても": "即使…也…",
    "けど": "但是", "けれど": "但是", "のに": "明明…却…", "ながら": "一边…一边…", "だけ": "只有",
    "しか": "只（后接否定）", "ほど": "程度：…左右", "って": "（口语）说是…", "な": "（接形容动词）…的",
    "ない": "否定：不…", "たい": "愿望：想要…", "た": "过去：…了", "だ": "断定：是…", "です": "礼貌的“是”",
    "ます": "礼貌体（无实际意义）", "れる": "被动／可能：被…；能…", "られる": "被动／可能：被…；能…",
    "せる": "使役：让…", "させる": "使役：让…", "う": "意志：…吧", "よう": "意志／推测：…吧",
    "ず": "否定（书面）：不…", "ぬ": "否定（书面）：不…", "らしい": "好像…", "そう": "听说／看起来…",
    "べき": "应该…", "まい": "不会…吧",
}
COUNTER_ZH = {"月": "月", "日": "日", "人": "人", "件": "件", "円": "日元", "年": "年", "回": "次", "歳": "岁",
              "階": "楼", "万": "万", "億": "亿", "割": "成", "倍": "倍", "時": "点", "分": "分", "秒": "秒",
              "店": "家店", "台": "台", "本": "根／瓶", "枚": "张", "点": "件（作品）", "種": "种", "週": "周",
              "号": "号", "位": "名", "位置": "位置", "km": "公里", "幅": "幅", "球": "颗灯"}
DAY_READ = {1: "ついたち", 2: "ふつか", 3: "みっか", 4: "よっか", 5: "いつか", 6: "むいか", 7: "なのか",
            8: "ようか", 9: "ここのか", 10: "とおか", 14: "じゅうよっか", 20: "はつか", 24: "にじゅうよっか"}
MONTH_READ = {4: "しがつ", 7: "しちがつ", 9: "くがつ"}
SYMBOL_ZH = {"「": "引号：引用某人的话或名称", "」": "引号结束", "『": "书名号：作品名", "』": "书名号结束",
             "…": "省略：话没说完，引人往下读", "・": "并列分隔（相当于“、”）", "？": "疑问", "！": "感叹"}
_tk = None


def _tokenizer():
    global _tk
    if _tk is None:
        from janome.tokenizer import Tokenizer
        _tk = Tokenizer()
    return _tk


def _hira(kata):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in kata)


def tokenize(title):
    """見出しを語に分けて [{s,r,b,p,k,m,lv,note}] を返す。m が None の語は後で機械翻訳する。"""
    try:
        raw = list(_tokenizer().tokenize(title))
    except ImportError:
        return []
    d = jlpt_dict()
    toks = []
    for i, t in enumerate(raw):
        pos = t.part_of_speech.split(",")
        s, base = t.surface, (t.base_form if t.base_form != "*" else t.surface)
        reading = _hira(t.reading) if t.reading != "*" else ""
        prev = toks[-1] if toks else None
        # 数字＋助数詞（12＋月、二＋輪 など）はまとめる
        if (pos[0] == "名詞" and (pos[1] in ("数", "接尾") or s in COUNTER_ZH) and prev and prev["k"] == "num"
                and re.search(r"[0-9０-９一二三四五六七八九十百千万億]$", prev["s"])):  # 「12月」+「6」は別の語
            num, unit = prev["s"], COUNTER_ZH.get(s)
            n = int(num) if num.isdigit() else None
            if s == "日" and n in DAY_READ:
                r = DAY_READ[n]
            elif s == "月" and n is not None:
                r = MONTH_READ.get(n, f"{num}がつ")
            else:
                r = prev.get("r", num) + reading
            prev.update(s=num + s, m=num + unit if unit else num + s)
            if KANJI.search(s):
                prev["r"] = r
            continue
        # 名詞＋接尾（外国＋人 など）は辞書にあればまとめる
        if pos[1] == "接尾" and prev and prev["k"] in ("n", "pn") and prev["s"] + s in d:
            e = d[prev["s"] + s]
            prev.update(s=prev["s"] + s, b=prev["s"] + s, r=e["kana"], m=e["zh"], lv=e["jlpt"], k="n")
            continue
        tok = {"s": s, "p": POS_ZH.get(pos[0], pos[0])}
        if KANJI.search(s):
            tok["r"] = READING_FIX.get(s, reading)
        if base != s:
            tok["b"] = base
        if pos[0] == "記号" or (pos[0] == "名詞" and pos[1] == "サ変接続" and not re.search(r"\w", s)):
            tok.update(k="sym", p="符号", m=SYMBOL_ZH.get(s, ""))
        elif pos[1] == "数" or re.fullmatch(r"[0-9０-９.,，]+", s):
            tok.update(k="num", p="数字", m=s)
        elif pos[0] in ("助詞", "助動詞"):
            tok.update(k="pt" if pos[0] == "助詞" else "ax", m=GRAMMAR.get(base, GRAMMAR.get(s, "")))
        else:
            e = d.get(s) or d.get(base)
            tok["k"] = "pn" if pos[1] == "固有名詞" else {"動詞": "v", "形容詞": "a"}.get(pos[0], "n")
            if e:
                tok.update(m=e["zh"], lv=e["jlpt"])
                if KANJI.search(s) and e["kana"] and "／" not in e["kana"] and (d.get(s) is e):
                    tok["r"] = e["kana"]
            elif tok["k"] == "pn":
                tok.update(p="专有名词·" + PROPER_ZH.get(pos[2], "名称"), m="")  # 固有名詞は機械翻訳しない（誤訳が多い）
            else:
                tok["m"] = None
        # 数字の後の「月」は「がつ」
        if s == "月" and prev and prev["k"] == "num":
            tok["r"] = "がつ"
        toks.append(tok)
    # 見出し特有の省略：文末（記号・空白の前）の サ変名詞／「へ」
    for i, tok in enumerate(toks):
        nxt = next((t for t in toks[i + 1:] if t["k"] != "sym" or t["s"] not in ("」", "』")), None)
        at_end = nxt is None or nxt["k"] == "sym"
        if at_end and tok["k"] == "n" and tok["p"] == "名词" and tok.get("m") is not None and raw and \
                any(r.surface == tok["s"] and r.part_of_speech.startswith("名詞,サ変接続") for r in raw):
            tok["note"] = "标题省略了「する／した」：表示“进行了／将进行 " + tok["s"] + "”"
        if at_end and tok["k"] == "pt" and tok["s"] == "へ":
            tok["note"] = "标题结尾的「へ」：表示“即将／计划…”"
        if at_end and tok["k"] == "pt" and tok["s"] == "か":
            tok["note"] = "标题结尾的「か」：表示“疑似／是否…”（尚未确定）"
    return [t for t in toks if t["s"].strip()]


def tokenize_items(items, cache_path):
    """items に tokens を付与。辞書に無い語はまとめて機械翻訳する。変更があれば True。"""
    todo = [x for x in items if x.get("tokens_v") != TOKENS_VERSION]
    if not todo:
        return False
    for x in todo:
        x["tokens"] = tokenize(x["title"])
        x["ruby"] = furigana(x["title"])
    unknown = sorted({t.get("b", t["s"]) for x in todo for t in x["tokens"]
                      if t.get("m") is None and not re.fullmatch(r"[A-Za-z0-9０-９ー]+", t["s"])})
    zh = translate(unknown, cache_path) if unknown else {}
    for x in todo:
        for t in x["tokens"]:
            if t.get("m") is None:
                w = t.get("b", t["s"])
                t["m"] = zh.get(w, "")
                if t["m"]:
                    t["mt"] = 1  # 機械翻訳
        x["tokens_v"] = TOKENS_VERSION
    return True


# ---- 見出しの構造解説（AI）-------------------------------------------------
EXPLAIN_PROMPT = """你是日语老师，学生是只学完五十音的中国人。请讲解下面每条日语新闻标题的句子结构，帮助他看懂。
每条输出一个 JSON 对象：
{"gist": "通俗的中文大意（一句话）",
 "chunks": [["日语意群原文", "中文意思", "作用：主语／谓语／对象／地点／时间／修饰→被修饰的词／引用／标签 等"]],
 "grammar": [["语法点或词形", "中文说明"]],
 "note": "可选：标题写法技巧、非日本新闻、人名被误当地名等提醒，没有就省略"}
意群按原文顺序切分，拼起来应等于原标题。只输出 JSON 数组（顺序与输入一致），不要代码块或其他文字。
标题："""


def _claude_json(prompt, timeout=600):
    r = subprocess.run(["claude", "-p", "--model", "haiku", prompt], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError((r.stdout + r.stderr).strip()[:200])
    return parse_lesson_json(r.stdout, array=True)  # 未エスケープの " を直してから読む


def explain_items(items, path, batch=8):
    """items に explain（大意・意群・文法点）を付与。未解説の見出しは claude CLI で生成（使えなければスキップ）。"""
    global _cli_ok
    cache = json.loads(path.read_text()) if path.exists() else {}
    todo = [t for t in dict.fromkeys(x["title"] for x in items) if t not in cache]
    if todo and _cli_ok:
        print(f"正在生成句子讲解 {len(todo)} 条（claude CLI，每批 {batch} 条）…", file=sys.stderr, flush=True)
    for i in range(0, len(todo), batch):
        if not _cli_ok:
            break
        chunk = todo[i:i + batch]
        try:
            out = _claude_json(EXPLAIN_PROMPT + "\n" + json.dumps(chunk, ensure_ascii=False))
            got = {t: e for t, e in zip(chunk, out) if isinstance(e, dict) and e.get("gist") and e.get("chunks")}
            cache.update(got)
            path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
            print(f"句子讲解 {min(i + batch, len(todo))}/{len(todo)}（本批成功 {len(got)} 条）", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"句子讲解の生成に失敗（claude CLI）: {e}", file=sys.stderr)
            _cli_ok = False
    changed = False
    for x in items:
        e = cache.get(x["title"])
        if e and x.get("explain", {}).get("gist") != e["gist"]:
            x["explain"] = dict(e, ruby=[furigana(c[0]) for c in e["chunks"]])
            changed = True
    return changed


def enrich(items, cache_path):
    """items に ruby / zh / tokens / explain を付与する（in place）。変更があれば True。"""
    zh = translate([x["title"] for x in items if not x.get("zh")], cache_path)
    changed = tokenize_items(items, cache_path)
    changed = explain_items(items, cache_path.parent / "explanations.json") or changed
    for x in items:
        if "ruby" not in x:
            x["ruby"] = furigana(x["title"])
            changed = True
        if not x.get("zh") and zh.get(x["title"]):
            x["zh"] = zh[x["title"]]
            changed = True
    return changed


# ==========================================================================
# 3. 閲覧ページ生成
# ==========================================================================

PAGE_SECTIONS = {
    "imm_local": {"label": "移民政策", "ja": "移民・外国人政策", "area": "东京及周边", "kind": "imm"},
    "imm_national": {"label": "移民政策", "ja": "移民・外国人政策", "area": "全国・国家政策", "kind": "imm"},
    "evt_tokyo": {"label": "社交活动", "ja": "社交イベント", "area": "东京都内", "kind": "evt"},
    "evt_around": {"label": "社交活动", "ja": "社交イベント", "area": "神奈川・埼玉・千叶", "kind": "evt"},
}


def load(news_dir):
    days = {}
    cache = news_dir / "translations_zh.json"
    for f in sorted(news_dir.glob("*/tokyo_news_*.json")):
        items = json.loads(f.read_text())
        if enrich(items, cache):  # 付与結果を JSON にも保存して次回以降は再計算しない
            f.write_text(json.dumps(items, ensure_ascii=False, indent=1))
        days[f.parent.name] = items
    return days


def build(news_dir):
    days = load(news_dir)
    try:
        marks = grammar_marks(load_syllabus(), load_progress(USER_DIR / PROGRESS_NAME), days)
    except ValueError as e:  # 進度・大綱が壊れていてもページは作る
        print(f"警告：无法标记学过的语法点：{e}", file=sys.stderr)
        marks = {}
    data = json.dumps({"days": days, "sections": PAGE_SECTIONS, "marks": marks},
                      ensure_ascii=False).replace("</", "<\\/")
    out = news_dir / "index.html"
    out.write_text(TEMPLATE.replace("/*__BASE_CSS__*/", BASE_CSS).replace("/*__DATA__*/null", data))
    (news_dir / "vocab.html").write_text(VOCAB_TEMPLATE.replace("/*__BASE_CSS__*/", BASE_CSS))
    (news_dir / "lesson.html").write_text(LESSON_TEMPLATE.replace("/*__BASE_CSS__*/", BASE_CSS))
    return out, len(days)


BASE_CSS = r"""
:root {
  --bg: #f6f5f1; --surface: #ffffff; --text: #1d1d1b; --muted: #6b6a65; --line: #e4e2dc;
  --imm: #b4442e; --imm-soft: #f6e4df; --evt: #2b6a8f; --evt-soft: #e0edf4;
  --chip: #efede7; --accent: #1d1d1b;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161615; --surface: #20201e; --text: #ecebe6; --muted: #9c9a93; --line: #34332f;
    --imm: #e58a74; --imm-soft: #3a2620; --evt: #7fb6d6; --evt-soft: #1e2f3a;
    --chip: #2b2a27; --accent: #ecebe6;
  }
}
:root[data-theme="dark"] {
  --bg: #161615; --surface: #20201e; --text: #ecebe6; --muted: #9c9a93; --line: #34332f;
  --imm: #e58a74; --imm-soft: #3a2620; --evt: #7fb6d6; --evt-soft: #1e2f3a;
  --chip: #2b2a27; --accent: #ecebe6;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", "Yu Gothic", sans-serif; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 32px 16px 64px; }
header { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 16px;
  border-bottom: 2px solid var(--accent); padding-bottom: 16px; }
h1 { font-size: 28px; margin: 0; letter-spacing: .02em; }
.sub { color: var(--muted); font-size: 13px; margin-top: 4px; }
select, input { font: inherit; color: var(--text); background: var(--surface); border: 1px solid var(--line);
  border-radius: 8px; padding: 8px 10px; }
.chip { border: 1px solid var(--line); background: var(--chip); color: var(--text); border-radius: 999px;
  padding: 6px 14px; font: inherit; font-size: 13px; cursor: pointer; }
.chip[aria-pressed="true"] { background: var(--accent); color: var(--bg); border-color: var(--accent); }
.empty { color: var(--muted); padding: 24px; text-align: center; border: 1px dashed var(--line); border-radius: 10px; }
mark { background: #f3d36b; color: #1d1d1b; border-radius: 2px; }
footer { margin-top: 40px; color: var(--muted); font-size: 12px; }
textarea { font: inherit; color: var(--text); background: var(--surface); border: 1px solid var(--line);
  border-radius: 8px; padding: 8px 10px; width: 100%; resize: vertical; }
.navlink { color: var(--text); font-size: 14px; font-weight: 600; text-decoration: none; border: 1px solid var(--line);
  background: var(--surface); border-radius: 999px; padding: 6px 14px; white-space: nowrap; }
.btn { font: inherit; font-size: 14px; border-radius: 8px; padding: 8px 16px; cursor: pointer;
  border: 1px solid var(--line); background: var(--surface); color: var(--text); }
.btn.primary { background: var(--accent); color: var(--bg); border-color: var(--accent); }
.btn.danger { color: var(--imm); }
.btn:disabled { opacity: .45; cursor: default; }
.toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); background: var(--accent); color: var(--bg);
  padding: 10px 18px; border-radius: 999px; font-size: 14px; opacity: 0; transition: opacity .2s; pointer-events: none; z-index: 30; }
.toast.show { opacity: 1; }
.notice { background: var(--imm-soft); color: var(--text); border-radius: 10px; padding: 10px 14px; font-size: 13px; margin-top: 16px; }
.notice code { font-size: 12px; }
u.lm { text-decoration: underline 2px #e0a800; text-underline-offset: 4px; }
.lmtags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.lmtag { font-size: 11px; color: #8a6514; background: #f3ecd9; border-radius: 6px; padding: 0 6px; cursor: default; }
rt { font-size: 10px; font-weight: 400; color: var(--muted); letter-spacing: 0; }
.lvb { display: inline-block; font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 6px;
  vertical-align: middle; letter-spacing: .02em; background: var(--chip); color: var(--muted); }
.lvb.N5 { background: #e3f1e4; color: #2f7a3a; } .lvb.N4 { background: #e0edf4; color: #2b6a8f; }
.lvb.N3 { background: #f3ecd9; color: #8a6514; } .lvb.N2 { background: #f6e4df; color: #b4442e; }
.lvb.N1 { background: #ece3f3; color: #6d3f91; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .lvb { filter: brightness(.75) saturate(1.2); } }
:root[data-theme="dark"] .lvb { filter: brightness(.75) saturate(1.2); }
"""


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>东京新闻日报</title>
<style>
/*__BASE_CSS__*/
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 24px 0; }
.stat { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px;
  cursor: pointer; text-align: left; font: inherit; color: inherit; }
.stat[aria-pressed="true"] { outline: 2px solid var(--accent); }
.stat .n { font-size: 28px; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; }
.stat .k { font-size: 12px; font-weight: 600; }
.stat .a { font-size: 12px; color: var(--muted); }
.stat.imm .k { color: var(--imm); } .stat.evt .k { color: var(--evt); }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 20px; }
.toolbar input { flex: 1 1 220px; }
section { margin-top: 28px; }
section h2 { font-size: 17px; margin: 0 0 12px; display: flex; align-items: center; gap: 8px; }
section h2 .tag { font-size: 12px; padding: 2px 8px; border-radius: 6px; font-weight: 600; }
section.imm h2 .tag { background: var(--imm-soft); color: var(--imm); }
section.evt h2 .tag { background: var(--evt-soft); color: var(--evt); }
section h2 .cnt { color: var(--muted); font-weight: 400; font-size: 13px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.card { display: flex; flex-direction: column; gap: 10px; background: var(--surface); border: 1px solid var(--line);
  border-left: 4px solid var(--evt); border-radius: 10px; padding: 14px 16px; transition: box-shadow .12s; }
section.imm .card { border-left-color: var(--imm); }
.card:hover { box-shadow: 0 6px 18px rgba(0,0,0,.08); }
.card .t ::selection, .card .t::selection { background: #f3d36b; color: #1d1d1b; }
.card .m a { color: var(--muted); text-decoration: none; white-space: nowrap; }
.card .m a:hover { color: var(--text); }
.card .t { font-weight: 600; font-size: 15px; line-height: 2.1; }
body.no-kana .card .t { line-height: 1.5; }
body.no-kana rt { display: none; }
.card .zh { font-size: 14px; line-height: 1.55; color: var(--text); background: var(--chip);
  border-radius: 6px; padding: 6px 10px; }
body.no-zh .card .zh { display: none; }
.toggles { display: flex; gap: 8px; margin-left: auto; }
.jp { display: block; font-size: 11px; color: var(--muted); font-weight: 400; }
.card .m { display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 12px; margin-top: auto; }
.card .m .src { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card .tools { display: flex; gap: 8px; }
.card .tools button { font: inherit; font-size: 12px; border: 1px solid var(--line); background: var(--chip); color: var(--text);
  border-radius: 999px; padding: 3px 12px; cursor: pointer; }
.card .tools button[aria-expanded="true"] { background: var(--accent); color: var(--bg); border-color: var(--accent); }
.ex { display: none; border-top: 1px dashed var(--line); padding-top: 10px; font-size: 13px; }
.card.exopen .ex { display: block; }
.ex .gist { font-size: 15px; font-weight: 600; line-height: 1.6; background: var(--evt-soft); border-radius: 8px; padding: 8px 12px; }
.ex .gist::before { content: "大意　"; font-size: 11px; color: var(--evt); font-weight: 700; }
.ex .chunks { margin-top: 10px; display: grid; gap: 6px; }
.ex .ck { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; align-items: center; border: 1px solid var(--line);
  border-radius: 8px; padding: 6px 10px; }
.ex .ck .ja { font-size: 15px; line-height: 2; }
.ex .ck .zh { line-height: 1.5; }
.ex .ck .role { display: inline-block; font-size: 11px; color: var(--muted); border: 1px solid var(--line); border-radius: 5px; padding: 0 5px; margin-top: 3px; }
.ex .ck .role.mod { color: #b07800; border-color: #e8c66a; }
.ex .ck.mod { border-left: 3px solid #e0a100; }
.ex h4 { margin: 12px 0 4px; font-size: 12px; color: var(--muted); font-weight: 600; }
.ex ul { margin: 0; padding-left: 18px; line-height: 1.8; }
.ex .exnote { margin-top: 8px; color: #b07800; }
.ex .none { color: var(--muted); }
@media (max-width: 720px) { .ex .ck { grid-template-columns: 1fr; gap: 2px; } }
.bd { display: none; border-top: 1px dashed var(--line); padding-top: 10px; }
.card.open .bd { display: block; }
.bd .toks { display: flex; flex-wrap: wrap; gap: 6px 4px; align-items: flex-end; }
.tok { display: inline-flex; flex-direction: column; align-items: center; border-radius: 6px; padding: 3px 5px 4px;
  border: 1px solid transparent; cursor: pointer; max-width: 120px; text-align: center; }
.tok:hover, .tok.on { border-color: var(--line); background: var(--chip); }
.tok .r { font-size: 10px; color: var(--muted); min-height: 13px; line-height: 13px; }
.tok .w { font-size: 16px; font-weight: 600; line-height: 1.3; }
.tok .g { font-size: 11px; color: var(--muted); line-height: 1.3; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; max-width: 110px; }
.tok.pt .w, .tok.ax .w { color: var(--imm); }
.tok.pt .g, .tok.ax .g { color: var(--imm); }
.tok.v .w { color: #2f7a3a; } .tok.a .w { color: #8a6514; }
.tok.pn .w { text-decoration: underline dotted var(--muted); text-underline-offset: 3px; }
.tok.sym { cursor: default; padding-left: 1px; padding-right: 1px; }
.tok.sym .w { color: var(--muted); font-weight: 400; }
.tok .note { width: 6px; height: 6px; border-radius: 50%; background: #e0a100; margin-top: 2px; }
.bd .detail { margin-top: 10px; font-size: 13px; background: var(--chip); border-radius: 8px; padding: 10px 12px; line-height: 1.7; }
.bd .detail .dw { font-size: 18px; font-weight: 700; }
.bd .detail .tag { font-size: 11px; color: var(--muted); border: 1px solid var(--line); border-radius: 5px; padding: 0 5px; margin-left: 6px; }
.bd .detail .hint { color: #b07800; }
.bd .detail .add { margin-top: 6px; font: inherit; font-size: 12px; border-radius: 6px; padding: 3px 10px; cursor: pointer;
  border: 1px solid var(--line); background: var(--surface); color: var(--text); }
.bd .legend { font-size: 11px; color: var(--muted); margin-top: 8px; }
.bd .legend b { font-weight: 600; }
details.tips { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 12px 16px; margin-bottom: 20px; }
details.tips summary { cursor: pointer; font-weight: 600; font-size: 14px; }
details.tips table { border-collapse: collapse; margin-top: 10px; font-size: 13px; width: 100%; }
details.tips td { border-top: 1px solid var(--line); padding: 7px 8px; vertical-align: top; }
details.tips td:first-child { white-space: nowrap; font-weight: 600; width: 1%; }
details.tips td:nth-child(2) { color: var(--muted); }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .tok.v .w { color: #7fc48a; }
  :root:not([data-theme="light"]) .tok.a .w { color: #d9b45a; } }
.fetchbox { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; margin-top: 16px; font-size: 13px; }
.fetchbox #fetchMsg { font-weight: 600; }
.fetchbox pre { margin: 6px 0 0; white-space: pre-wrap; font: 12px/1.6 ui-monospace, Menlo, monospace; color: var(--muted); }
#fetchBtn:disabled { opacity: .6; cursor: progress !important; }
#pop { position: absolute; z-index: 20; display: none; background: var(--accent); color: var(--bg); border: 0;
  border-radius: 999px; padding: 6px 14px; font: inherit; font-size: 13px; cursor: pointer; box-shadow: 0 4px 14px rgba(0,0,0,.2); }
dialog { border: 1px solid var(--line); border-radius: 14px; background: var(--surface); color: var(--text);
  padding: 20px; width: min(480px, calc(100vw - 32px)); }
dialog::backdrop { background: rgba(0,0,0,.35); }
dialog h3 { margin: 0 0 14px; font-size: 17px; }
dialog label { display: block; font-size: 12px; color: var(--muted); margin: 10px 0 4px; }
dialog input { width: 100%; }
dialog .word { font-size: 22px; font-weight: 700; }
dialog .row { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
dialog .hint { font-size: 12px; color: var(--muted); min-height: 1em; margin-top: 8px; }
@media (max-width: 720px) {
  .stats { grid-template-columns: repeat(2, 1fr); }
  h1 { font-size: 22px; }
  .grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>东京及周边新闻日报</h1>
      <div class="sub">社交活动 ／ 移民政策 ・ 来源：Google 新闻（日文版）・ 假名注音为自动生成，个别读音可能有误</div>
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <button class="navlink" id="fetchBtn" type="button" style="cursor:pointer;font:inherit;font-weight:600">🔄 抓取最新新闻</button>
      <a class="navlink" href="lesson.html">今日一课 <span id="lstatus"></span></a>
      <a class="navlink" href="vocab.html">单词本 <span id="vcount"></span></a>
      <label class="sub">日期&nbsp;<select id="day"></select></label>
    </div>
  </header>
  <div class="fetchbox" id="fetchBox" hidden><div id="fetchMsg"></div><pre id="fetchLog"></pre></div>
  <div class="notice" id="fileNotice" hidden>当前是直接打开的文件，「抓取最新新闻」和划词加入单词本需要先启动本地服务：
    <code>python3 scripts/fetch_news.py --serve</code>，然后访问 <code>http://localhost:8765</code></div>

  <div class="stats" id="stats"></div>

  <details class="tips">
    <summary>📖 看懂日语新闻标题的 8 个窍门（点开）</summary>
    <table>
      <tr><td>名词结尾</td><td>新宿から<b>通報</b></td><td>标题常省略「する／した」。以「通報・開催・決定」等结尾 = “进行了／将进行”。例：从新宿<b>发出了举报</b></td></tr>
      <tr><td>～へ</td><td>12月6日開催<b>へ</b></td><td>结尾的「へ」= “即将／计划…”。例：<b>将于</b>12月6日举办</td></tr>
      <tr><td>～か</td><td>殺人関与<b>か</b></td><td>结尾的「か」= “疑似／可能”，事情还没确定</td></tr>
      <tr><td>「 」</td><td><b>「</b>同胞が犯罪<b>」</b></td><td>引号里是当事人说的话，或是活动、店铺的名字</td></tr>
      <tr><td>が・を・に・で</td><td>知事<b>が</b>主席<b>と</b>会談</td><td>助词决定词语的角色：が=谁做，を=做什么，に/で=在哪里/何时，と=和谁</td></tr>
      <tr><td>…・、</td><td>交流会<b>…</b>東京都</td><td>「…」后面常接主办方或地点；「、」常代替“并且”，把两件事连在一起</td></tr>
      <tr><td>【 】［ ］</td><td><b>【</b>杉並区<b>】</b></td><td>方括号是栏目或地区标签，不是句子的一部分；末尾（ ）里常是媒体名</td></tr>
      <tr><td>片假名</td><td>イベント・マルシェ</td><td>片假名多是外来语，读出来常能猜到英语原词：イベント=event（活动）</td></tr>
    </table>
  </details>

  <div class="toolbar">
    <button class="chip" data-kind="all" aria-pressed="true">全部</button>
    <button class="chip" data-kind="imm" aria-pressed="false">移民政策</button>
    <button class="chip" data-kind="evt" aria-pressed="false">社交活动</button>
    <input id="q" type="search" placeholder="搜索日文或中文（例：新宿、越南、市集）">
    <div class="toggles">
      <button class="chip opt" id="kana" aria-pressed="true">假名注音</button>
      <button class="chip opt" id="zh" aria-pressed="true">中文翻译</button>
    </div>
  </div>

  <main id="list"></main>
  <footer id="foot"></footer>
</div>

<button id="pop" type="button">＋ 加入单词本</button>
<dialog id="dlg">
  <form method="dialog" id="vform">
    <h3>加入单词本</h3>
    <label>日语</label><input name="word" class="word" required lang="ja">
    <label>假名</label><input name="kana" lang="ja" placeholder="自动生成中…">
    <label>中文释义</label><input name="zh" placeholder="自动翻译中…（可修改）">
    <label>等级</label><select name="jlpt" style="width:100%">
      <option>N5</option><option>N4</option><option>N3</option><option>N2</option><option>N1</option><option>级外</option></select>
    <label>例句（日语）</label><textarea name="sentence" rows="3" lang="ja"></textarea>
    <label>例句译文</label><textarea name="sentence_zh" rows="2"></textarea>
    <div class="hint" id="dhint"></div>
    <div class="row">
      <button class="btn" value="cancel" formnovalidate>取消</button>
      <button class="btn primary" value="save" id="dsave">保存</button>
    </div>
  </form>
</dialog>
<div class="toast" id="toast"></div>

<script>
const DATA = /*__DATA__*/null;
const S = DATA.sections, days = Object.keys(DATA.days).sort().reverse();
const state = { day: days[0], kind: "all", section: null, q: "" };
const $ = s => document.querySelector(s);
const esc = s => s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ruby = (x, q, hits) => (x.ruby || [[x.title, ""]]).map(([t, r]) =>
  r ? `<ruby>${ul(hl(t, q), hits)}<rt>${esc(r)}</rt></ruby>` : ul(hl(t, q), hits)).join("");
// 学过的语法点所在的片段加下划线
const ul = (h, hits) => (hits || []).reduce((s, w) => s.split(esc(w)).join(`<u class="lm">${esc(w)}</u>`), h);
const MARKS = DATA.marks || {};
const markTags = x => (MARKS[x.title] || []).map(m =>
  `<span class="lmtag" title="${esc(m.learned)} 学过">学过 · ${esc(m.title)}</span>`).join("");
const hl = (s, q) => { s = esc(s); if (!q) return s;
  const r = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"); return s.replace(r, m => `<mark>${m}</mark>`); };

$("#day").innerHTML = days.map(d => `<option>${d}</option>`).join("");
if (!days.length) $("#list").innerHTML = '<div class="empty">暂无数据，请先运行 scripts/fetch_news.py</div>';

function render() {
  if (!days.length) return;
  const items = DATA.days[state.day] || [];
  $("#stats").innerHTML = Object.entries(S).map(([k, s]) => {
    const n = items.filter(x => x.section === k).length;
    return `<button class="stat ${s.kind}" data-sec="${k}" aria-pressed="${state.section === k}">
      <div class="k">${s.label} <span class="jp">${s.ja}</span></div><div class="n">${n}</div><div class="a">${s.area}</div></button>`;
  }).join("");
  const q = state.q.trim().toLowerCase();
  let html = "", shown = 0;
  for (const [k, s] of Object.entries(S)) {
    if (state.kind !== "all" && s.kind !== state.kind) continue;
    if (state.section && state.section !== k) continue;
    const list = items.filter(x => x.section === k &&
      (!q || (x.title + x.source + (x.zh || "")).toLowerCase().includes(q)));
    shown += list.length;
    html += `<section class="${s.kind}"><h2><span class="tag">${s.label}</span>${s.area}
      <span class="cnt">${list.length} 条</span></h2>` +
      (list.length ? `<div class="grid">${list.map(x => `
        <article class="card" data-i="${items.indexOf(x)}">
          <div class="t" lang="ja">${ruby(x, state.q.trim(), (MARKS[x.title] || []).flatMap(m => m.hits))}</div>
          ${MARKS[x.title] ? `<div class="lmtags">${markTags(x)}</div>` : ""}
          ${x.zh ? `<div class="zh">${hl(x.zh, state.q.trim())}</div>` : ""}
          <div class="tools"><button type="button" class="exbtn" aria-expanded="false">🧠 句子讲解</button>
            ${x.tokens && x.tokens.length ? `<button type="button" class="bdbtn" aria-expanded="false">🔍 逐词拆解</button>` : ""}</div>
          <div class="ex"></div><div class="bd"></div>
          <div class="m"><span class="src">${esc(x.source)} ・ ${x.published.slice(5)}</span>
            <a href="${esc(x.link)}" target="_blank" rel="noopener">阅读原文 ↗</a></div>
        </article>`).join("")}</div>` : `<div class="empty">没有符合条件的新闻</div>`) + `</section>`;
  }
  $("#list").innerHTML = html;
  $("#foot").textContent = `${state.day} ・ 显示 ${shown} 条 ／ 共 ${items.length} 条 ・ 点「🧠 句子讲解」看句子结构，「🔍 逐词拆解」看每个词；选中标题里的词语可加入单词本`;
}

// ---- 逐词拆解 ----------------------------------------------------------
const KIND = { n: "名词", pn: "专有名词", v: "动词", a: "形容词", pt: "助词", ax: "助动词", num: "数字", sym: "符号" };
function breakdownHtml(x) {
  return `<div class="toks" lang="ja">${x.tokens.map((t, i) => `
    <span class="tok ${t.k}" data-t="${i}" title="${esc((t.m || "") + (t.note ? "\n" + t.note : ""))}">
      <span class="r">${esc(t.r || "")}</span><span class="w">${esc(t.s)}</span>
      <span class="g">${t.k === "sym" ? "" : esc(t.k === "pt" || t.k === "ax" ? (t.m || "").split("：")[0].split("（")[0]
        : t.k === "pn" && !t.m ? (t.p || "").split("·")[1] || "专有名词" : (t.m || "—").split(/[；;，,]/)[0])}</span>
      ${t.note ? '<span class="note"></span>' : ""}
    </span>`).join("")}</div>
    <div class="detail">点上面任意一个词，查看详细解释。<span style="color:#b07800">● 黄点</span> = 标题特有写法</div>
    <div class="legend">颜色：<b style="color:var(--imm)">红色＝助词／语法</b> ・ <b style="color:#2f7a3a">绿色＝动词</b> ・ <b style="color:#8a6514">黄褐＝形容词</b> ・ 虚线＝人名地名等专有名词</div>`;
}
function detailHtml(t) {
  const word = t.b || t.s, canAdd = !["pt", "ax", "sym", "num"].includes(t.k);
  return `<div><span class="dw" lang="ja">${esc(t.s)}</span>
      ${t.r ? `<span lang="ja" style="margin-left:6px;color:var(--evt)">${esc(t.r)}</span>` : ""}
      <span class="tag">${esc(t.p || KIND[t.k] || "")}</span>${t.lv ? `<span class="lvb ${esc(t.lv)}" style="margin-left:6px">${esc(t.lv)}</span>` : ""}</div>
    ${t.b ? `<div>原形：<span lang="ja">${esc(t.b)}</span>（句中是变形后的样子）</div>` : ""}
    <div>${t.k === "pt" || t.k === "ax" ? "语法作用" : "意思"}：${esc(t.m || (t.k === "pn" ? "这是" + ((t.p || "").split("·")[1] || "专有名词") + "，按读音理解即可" : "（暂无释义）"))}${t.mt ? ' <span class="tag">机器翻译</span>' : ""}</div>
    ${t.note ? `<div class="hint">💡 ${esc(t.note)}</div>` : ""}
    ${canAdd ? `<button type="button" class="add" data-word="${esc(word)}">＋ 加入单词本</button>` : ""}`;
}
const rubyHtml = segs => segs.map(([t, r]) => r ? `<ruby>${esc(t)}<rt>${esc(r)}</rt></ruby>` : esc(t)).join("");
function explainHtml(x) {
  const e = x.explain;
  if (!e) return `<div class="none">这条还没有句子讲解。登录 claude 命令行（在终端运行 <code>claude</code>）后，抓取新闻时会自动生成。</div>`;
  return `<div class="gist">${esc(e.gist)}</div>
    <h4>按意群拆开（从左到右就是原文顺序）</h4>
    <div class="chunks">${e.chunks.map((c, i) => {
      const mod = /^修饰/.test(c[2] || "");
      return `<div class="ck${mod ? " mod" : ""}"><div class="ja" lang="ja">${e.ruby && e.ruby[i] ? rubyHtml(e.ruby[i]) : esc(c[0])}</div>
        <div><div class="zh">${esc(c[1])}</div>${c[2] ? `<span class="role${mod ? " mod" : ""}">${esc(c[2])}</span>` : ""}</div></div>`;
    }).join("")}</div>
    ${e.grammar && e.grammar.length ? `<h4>语法点</h4><ul>${e.grammar.map(g => `<li><b lang="ja">${esc(g[0])}</b>　${esc(g[1])}</li>`).join("")}</ul>` : ""}
    ${e.note ? `<div class="exnote">💡 ${esc(e.note)}</div>` : ""}`;
}
$("#list").addEventListener("click", e => {
  const card = e.target.closest(".card"); if (!card) return;
  const x = (DATA.days[state.day] || [])[+card.dataset.i];
  if (e.target.closest(".exbtn")) {
    const open = card.classList.toggle("exopen");
    e.target.closest(".exbtn").setAttribute("aria-expanded", open);
    if (open && !card.querySelector(".ex").innerHTML) card.querySelector(".ex").innerHTML = explainHtml(x);
    return;
  }
  if (e.target.closest(".bdbtn")) {
    const open = card.classList.toggle("open");
    e.target.closest(".bdbtn").setAttribute("aria-expanded", open);
    if (open && !card.querySelector(".bd").innerHTML) card.querySelector(".bd").innerHTML = breakdownHtml(x);
    return;
  }
  const tk = e.target.closest(".tok");
  if (tk && !tk.classList.contains("sym")) {
    card.querySelectorAll(".tok.on").forEach(n => n.classList.remove("on"));
    tk.classList.add("on");
    card.querySelector(".bd .detail").innerHTML = detailHtml(x.tokens[+tk.dataset.t]);
    return;
  }
  const add = e.target.closest(".bd .add");
  if (add) { picked = { word: add.dataset.word, item: x }; $("#pop").onclick(); }
});

$("#day").onchange = e => { state.day = e.target.value; state.section = null; render(); };
$("#q").oninput = e => { state.q = e.target.value; render(); };
for (const id of ["kana", "zh"]) {
  const b = document.getElementById(id), key = "news-" + id;
  const set = on => { b.setAttribute("aria-pressed", on); document.body.classList.toggle("no-" + id, !on);
    try { localStorage.setItem(key, on ? "1" : "0"); } catch (e) {} };
  let saved = null; try { saved = localStorage.getItem(key); } catch (e) {}
  set(saved !== "0");
  b.onclick = () => set(b.getAttribute("aria-pressed") !== "true");
}
document.querySelectorAll(".chip:not(.opt)").forEach(b => b.onclick = () => {
  state.kind = b.dataset.kind; state.section = null;
  document.querySelectorAll(".chip:not(.opt)").forEach(c => c.setAttribute("aria-pressed", c === b));
  render();
});
$("#stats").onclick = e => {
  const b = e.target.closest(".stat"); if (!b) return;
  state.section = state.section === b.dataset.sec ? null : b.dataset.sec;
  state.kind = "all";
  document.querySelectorAll(".chip:not(.opt)").forEach(c => c.setAttribute("aria-pressed", c.dataset.kind === "all"));
  render();
};
render();

// ---- 划词加入单词本 ----------------------------------------------------
const SERVED = location.protocol.startsWith("http");
const api = (path, opt) => fetch(path, opt).then(r => r.ok ? r.json() : r.text().then(t => Promise.reject(new Error(t))));
const toast = msg => { const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 2200); };
if (!SERVED) $("#fileNotice").hidden = false;
else {
  api("/api/vocab").then(d => $("#vcount").textContent = `(${d.words.length})`).catch(() => {});
  api("/api/lesson").then(d => $("#lstatus").textContent =
    d.status.current ? "· 未读" : d.status.due ? `· 复习 ${d.status.due}` : "✓").catch(() => {});
}

// ---- 手动抓取 ----------------------------------------------------------
async function pollFetch(first) {
  let j;
  try { j = await api("/api/fetch"); } catch (e) { return; }
  if (!j.running && !j.finished) return;  // 本次服务启动后还没抓取过
  if (first && !j.running) return;          // 打开页面时只显示进行中的任务
  const box = $("#fetchBox"), btn = $("#fetchBtn");
  box.hidden = false;
  $("#fetchLog").textContent = j.log.join("\n");
  if (j.running) {
    const sec = Math.round((Date.now() - new Date(j.started)) / 1000);
    $("#fetchMsg").textContent = `⏳ 正在抓取…（已用 ${sec} 秒）`;
    btn.disabled = true; btn.textContent = "⏳ 抓取中…";
    setTimeout(() => pollFetch(false), 2000);
  } else if (j.error) {
    $("#fetchMsg").textContent = "✗ 抓取失败"; btn.disabled = false; btn.textContent = "🔄 抓取最新新闻";
  } else {
    const r = j.result;
    $("#fetchMsg").textContent = `✓ 完成：精选 ${r.picked} 条，单词本新增 ${r.vocab_added} 个，2 秒后刷新页面…`;
    setTimeout(() => location.reload(), 2000);
  }
}
$("#fetchBtn").onclick = async () => {
  if (!SERVED) { $("#fileNotice").hidden = false; $("#fileNotice").scrollIntoView({ block: "center" }); return toast("请先启动本地服务"); }
  try { await api("/api/fetch", { method: "POST" }); } catch (e) { return toast("启动失败：" + e.message); }
  pollFetch(false);
};
if (SERVED) pollFetch(true);

let picked = null, pending = null, looked = {};  // pending: 对话框打开时对应的新闻条目；looked: 词典查询结果
function readSelection() {
  const sel = getSelection();
  if (!sel.rangeCount || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0), node = range.commonAncestorContainer;
  const t = (node.nodeType === 1 ? node : node.parentElement).closest(".card .t");
  if (!t) return null;
  const frag = range.cloneContents();
  frag.querySelectorAll("rt").forEach(n => n.remove());  // 注音不算入选中的词
  const word = frag.textContent.replace(/\s+/g, "").trim();
  if (!word || word.length > 30) return null;
  const x = (DATA.days[state.day] || [])[+t.closest(".card").dataset.i];
  return { word, item: x, rect: range.getBoundingClientRect() };
}
function showPop() {
  picked = readSelection();
  const pop = $("#pop");
  if (!picked) { pop.style.display = "none"; return; }
  pop.style.display = "block";
  pop.style.left = Math.max(8, Math.min(innerWidth - pop.offsetWidth - 8,
    picked.rect.left + scrollX + picked.rect.width / 2 - pop.offsetWidth / 2)) + "px";
  pop.style.top = (picked.rect.bottom + scrollY + 8) + "px";
}
document.addEventListener("mouseup", e => { if (e.target.id !== "pop" && !e.target.closest?.("dialog")) setTimeout(showPop, 0); });
document.addEventListener("touchend", () => setTimeout(showPop, 300));
document.addEventListener("selectionchange", () => { if (getSelection().isCollapsed) $("#pop").style.display = "none"; });
$("#pop").addEventListener("mousedown", e => e.preventDefault());  // 保持选区

$("#pop").onclick = async () => {
  $("#pop").style.display = "none";
  if (!picked) return;
  const f = $("#vform"), { word, item } = picked;
  pending = item; looked = {};
  f.reset(); f.jlpt.value = "级外";
  f.word.value = word; f.sentence.value = item.title; f.sentence_zh.value = item.zh || "";
  $("#dhint").textContent = SERVED ? "正在生成假名与释义…" : "未启动本地服务，无法保存（见页面顶部说明）";
  $("#dsave").disabled = !SERVED;
  $("#dlg").showModal();
  if (!SERVED) return;
  try {
    const r = await api("/api/lookup", { method: "POST", body: JSON.stringify({ word }) });
    if (f.word.value !== word) return;
    looked = r;
    if (!f.kana.value) f.kana.value = r.kana || "";
    if (!f.zh.value) f.zh.value = r.zh || "";
    f.jlpt.value = r.jlpt || "级外";
    $("#dhint").textContent = r.exists ? "这个词已在单词本中，保存会更新内容"
      : r.pos ? `JLPT 词典收录 ・ ${r.jlpt} ・ ${r.pos}` : "词典未收录，释义为机器翻译，仅供参考";
  } catch (e) { $("#dhint").textContent = "自动查询失败，请手动填写"; }
};
$("#dlg").addEventListener("close", async () => {
  if ($("#dlg").returnValue !== "save" || !pending) return;
  const f = $("#vform"), it = pending;
  const body = { word: f.word.value.trim(), kana: f.kana.value.trim(), zh: f.zh.value.trim(), jlpt: f.jlpt.value,
    ...(looked.pos ? { pos: looked.pos, ex: looked.ex, ex_zh: looked.ex_zh } : {}),
    sentence: f.sentence.value.trim(), sentence_zh: f.sentence_zh.value.trim(),
    source: `${it.source} ${it.published.slice(0, 10)}`, link: it.link };
  if (!body.word) return;
  try {
    const r = await api("/api/vocab", { method: "POST", body: JSON.stringify(body) });
    toast(`「${body.word}」已加入单词本（共 ${r.count} 个）`);
    $("#vcount").textContent = `(${r.count})`;
    getSelection().removeAllRanges();
  } catch (e) { toast("保存失败：" + e.message); }
});
</script>
</body>
</html>
"""


# ==========================================================================
# 4. 単語帳（news/vocab.md）とローカルサーバー
# ==========================================================================

# Markdown 上のフィールド名（表示順）
VOCAB_FIELDS = [("kana", "假名"), ("zh", "释义"), ("jlpt", "等级"), ("pos", "词性"),
                ("sentence", "例句"), ("sentence_zh", "例句译文"), ("ex", "词典例句"), ("ex_zh", "词典例句译文"),
                ("source", "出处"), ("link", "链接"), ("origin", "添加方式"),
                ("added", "添加日期"), ("level", "熟练度"), ("due", "下次复习")]
IGNORE_HEADING = "# 不再自动添加"  # 删除过的词记录在这里，自动添加时跳过
VOCAB_HEADER = ("# 单词本\n\n"
                "> 由新闻页面划词添加，也可以直接编辑本文件："
                "每个 `## ` 标题是一个单词，下面的 `- 字段：值` 是它的信息。\n")
AUTO_LEVELS = ("N5", "N4")  # 自動で単語帳に追加する等級
_vocab_lock = threading.Lock()

# ---- JLPT 辞書 ----------------------------------------------------------
JLPT_URL = "https://raw.githubusercontent.com/5mdld/anki-jlpt-decks/HEAD/deck-source/notes.csv"
JLPT_PATH = ROOT / "data" / "jlpt_vocab.json"
VOCAB_PATH = USER_DIR / "vocab.md"
JLPT_LEVELS = ["N5", "N4", "N3", "N2", "N1"]
_jlpt = None


def _strip_html(t):
    return html.unescape(re.sub(r"<[^>]+>|\[sound:[^\]]*\]", "", t)).strip()


def _build_jlpt():
    """Anki 牌组の notes.csv（タブ区切り）から {単語: {jlpt, kana, zh, pos, ex, ex_zh}} を作る。"""
    print("JLPT 辞書をダウンロード中…", file=sys.stderr)
    req = urllib.request.Request(JLPT_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = r.read().decode("utf-8")
    words = {}
    for row in csv.reader(io.StringIO(text), delimiter="\t"):
        if len(row) < 15 or row[0].startswith("#"):
            continue
        m = re.search(r"::\d-N(\d)", row[1])
        word = _strip_html(row[3])
        if not m or not word:
            continue
        e = {"jlpt": f"N{m.group(1)}", "kana": _strip_html(row[6]), "zh": _strip_html(row[7]),
             "pos": _strip_html(row[5]), "ex": _strip_html(row[12]), "ex_zh": _strip_html(row[14])}
        if re.fullmatch(r"[ァ-ヺー・]+", word):
            e["kana"] = word  # 外来語の読み欄は語源（英語など）なので表記そのものを使う
        old = words.get(word)
        if old is None or JLPT_LEVELS.index(e["jlpt"]) < JLPT_LEVELS.index(old["jlpt"]):
            words[word] = e  # 同じ語が複数等級にある場合は易しい方を採用
        elif old["jlpt"] == e["jlpt"] and e["kana"] not in old["kana"].split("／"):
            old["kana"] += "／" + e["kana"]
            old["zh"] += "；" + e["zh"]
    JLPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    JLPT_PATH.write_text(json.dumps({"source": "https://github.com/5mdld/anki-jlpt-decks",
                                     "license": "CC BY-NC 4.0", "words": words}, ensure_ascii=False))
    print(f"JLPT 辞書: {len(words)} 語 → {JLPT_PATH}", file=sys.stderr)


def jlpt_dict():
    global _jlpt
    if _jlpt is None:
        if not JLPT_PATH.exists():
            _build_jlpt()
        _jlpt = json.loads(JLPT_PATH.read_text())["words"]
    return _jlpt


def jlpt_words_in(text):
    """見出しを辞書で最長一致分割し、(単語, 辞書項目) を返す。ひらがなのみの語は誤検出が多いので対象外。"""
    d, out, i = jlpt_dict(), [], 0
    while i < len(text):
        for n in range(min(10, len(text) - i), 0, -1):
            w = text[i:i + n]
            kata = re.fullmatch(r"[ァ-ヺー]+", w)
            # カタカナ語は前後もカタカナなら長い外来語の一部（ジャパン→パン 等）なので除外
            if kata and ((i and re.match(r"[ァ-ヺー]", text[i - 1])) or re.match(r"[ァ-ヺー]", text[i + n:i + n + 1])):
                continue
            if w in d and re.search(r"[\u3400-\u9fff々ァ-ヺー]", w):
                out.append((w, d[w]))
                i += n
                break
        else:
            i += 1
    return out


def auto_add_vocab(items, vocab_path, levels=AUTO_LEVELS):
    """ニュース見出しに出てきた N5・N4 単語（2文字以上）を単語帳に追加する。追加数を返す。"""
    today = today_jst().isoformat()
    with _vocab_lock:
        words, ignored = vocab_load(vocab_path)
        have = {w["word"] for w in words} | ignored
        added = 0
        for x in items:
            for w, e in jlpt_words_in(x["title"]):
                if e["jlpt"] not in levels or len(w) < 2 or w in have:
                    continue
                have.add(w)
                words.append({"word": w, **e, "sentence": x["title"], "sentence_zh": x.get("zh", ""),
                              "source": f"{x['source']} {x['published'][:10]}", "link": x["link"],
                              "origin": "自动", "added": today, "level": 0, "due": today})
                added += 1
        if added:
            vocab_save(vocab_path, words, ignored)
    return added


def vocab_load(path):
    """(単語リスト, 自動追加しない語の集合) を返す。"""
    words, ignored, cur, in_ignore = [], set(), None, False
    label2key = {label: key for key, label in VOCAB_FIELDS}
    if not path.exists():
        return words, ignored
    for line in path.read_text().splitlines():
        if line.startswith("# "):
            in_ignore, cur = line.strip() == IGNORE_HEADING, None
        elif in_ignore:
            if line.startswith("- "):
                ignored.add(line[2:].strip())
        elif line.startswith("## "):
            cur = {"word": line[3:].strip()}
            words.append(cur)
        elif cur is not None and line.startswith("- ") and "：" in line:
            label, value = line[2:].split("：", 1)
            if label.strip() in label2key:
                cur[label2key[label.strip()]] = value.strip()
    for w in words:
        w["level"] = int(w.get("level") or 0) if str(w.get("level", "0")).isdigit() else 0
        w.setdefault("jlpt", jlpt_dict().get(w["word"], {}).get("jlpt", "级外"))
    return words, ignored


def vocab_save(path, words, ignored=()):
    out = [VOCAB_HEADER]
    for w in words:
        out.append(f"## {w['word']}")
        for key, label in VOCAB_FIELDS:
            v = str(w.get(key, "")).replace("\n", " ").strip()
            if v:
                out.append(f"- {label}：{v}")
        out.append("")
    if ignored:
        out += [IGNORE_HEADING, "", "> 这些词删除过，抓取新闻自动添加时会跳过；手动再加入单词本即可恢复。", ""]
        out += [f"- {w}" for w in sorted(ignored)] + [""]
    path.write_text("\n".join(out))


def kana_of(word):
    if not KANJI.search(word):
        return word
    try:
        return "".join(READING_FIX.get(x["orig"], x["hira"]) for x in _kakasi().convert(word))
    except ImportError:
        return ""


def today_jst():
    return datetime.now(JST).date()


def serve(news_dir, port):
    vocab_path = VOCAB_PATH
    cache_path = news_dir / "translations_zh.json"
    job = {"running": False, "log": [], "started": None, "finished": None, "result": None, "error": None}

    def run_job():
        def log(msg):
            job["log"].append(f"{datetime.now(JST):%H:%M:%S} {msg}")
            print(msg, file=sys.stderr)
        try:
            job["result"] = run_fetch(news_dir, log)
        except Exception as e:  # 失敗内容をページに表示する
            job["error"] = f"{type(e).__name__}: {e}"
            log("✗ 抓取失败：" + job["error"])
        finally:
            job["running"], job["finished"] = False, datetime.now(JST).isoformat(timespec="seconds")

    def start_job():
        with _vocab_lock:
            if job["running"]:
                return False
            job.update(running=True, log=[], result=None, error=None, finished=None,
                       started=datetime.now(JST).isoformat(timespec="seconds"))
        threading.Thread(target=run_job, daemon=True).start()
        return True

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(news_dir), **kw)

        def log_message(self, fmt, *args):
            if args and "/api/" in str(args[0]):
                sys.stderr.write("%s\n" % (fmt % args))

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")  # 再生成したページを確実に読み込ませる
            super().end_headers()

        def do_GET(self):
            if self.path.split("?")[0] == "/api/fetch":
                return self._json(job)
            if self.path.split("?")[0] == "/api/lesson":
                date = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("date", [None])[0]
                try:
                    with _vocab_lock:
                        return self._json(lesson_view(news_dir, USER_DIR, date, today_jst()))
                except ValueError as e:
                    return self._json({"error": str(e)}, 500)
            if self.path.split("?")[0] != "/api/vocab":
                return super().do_GET()
            with _vocab_lock:
                words, _ = vocab_load(vocab_path)
            for w in words:
                w["sentence_ruby"] = furigana(w["sentence"]) if w.get("sentence") else []
                w["ex_ruby"] = furigana(w["ex"]) if w.get("ex") else []
            self._json({"words": words, "today": today_jst().isoformat()})

        def do_POST(self):
            path = self.path.split("?")[0]
            if path == "/api/fetch":
                started = start_job()
                return self._json(dict(job, started_now=started))
            try:
                data = self._body()
            except ValueError:
                return self._json({"error": "bad json"}, 400)
            if path.startswith(("/api/lesson/", "/api/grammar/")):
                return self._lesson_post(path, data)
            word = str(data.get("word", "")).strip()
            if not word:
                return self._json({"error": "word is required"}, 400)

            if path == "/api/lookup":
                e = jlpt_dict().get(word)  # 辞書にあれば辞書の読み・釈義を優先
                res = dict(e) if e else {"jlpt": "级外", "kana": kana_of(word),
                                         "zh": translate([word], cache_path).get(word, "")}
                with _vocab_lock:
                    res["exists"] = any(w["word"] == word for w in vocab_load(vocab_path)[0])
                return self._json(res)

            with _vocab_lock:
                words, ignored = vocab_load(vocab_path)
                cur = next((w for w in words if w["word"] == word), None)
                if path == "/api/vocab":
                    entry = {k: str(data.get(k, "")).strip() for k, _ in VOCAB_FIELDS if k in data}
                    entry.setdefault("jlpt", jlpt_dict().get(word, {}).get("jlpt", "级外"))
                    ignored.discard(word)
                    if cur:
                        cur.update(entry)
                    else:
                        today = today_jst().isoformat()
                        words.append({"word": word, **entry, "origin": "手动", "added": today, "level": 0, "due": today})
                elif path == "/api/review" and cur:
                    # grade: 0=不认识 1=模糊 2=认识
                    grade = int(data.get("grade", 0))
                    cur["level"], cur["due"] = next_review(cur["level"], grade, today_jst())
                else:
                    return self._json({"error": "not found"}, 404)
                vocab_save(vocab_path, words, ignored)
            return self._json({"ok": True, "count": len(words)})

        def _lesson_post(self, path, data):
            prog_path, today = USER_DIR / PROGRESS_NAME, today_jst()
            try:
                if path == "/api/lesson/generate":
                    msgs = []

                    def log(msg):  # ページへの返答とサーバーの端末の両方に出す
                        msgs.append(msg)
                        print(f"{datetime.now(JST):%H:%M:%S} {msg}", file=sys.stderr, flush=True)
                    with _vocab_lock:
                        words = vocab_load(vocab_path)[0]
                    les = generate_today(news_dir, USER_DIR, today, words, ruby_fn=furigana, log=log,
                                         lock=_vocab_lock)
                    if not les:
                        return self._json({"error": msgs[-1] if msgs else "没有生成课程"}, 500)
                    with _vocab_lock:
                        return self._json(lesson_view(news_dir, USER_DIR, None, today))
                with _vocab_lock:
                    prog = load_progress(prog_path)
                    if path == "/api/lesson/done":
                        mark_done(prog, str(data.get("date", "")), today)
                        save_progress(prog_path, prog)
                        build(news_dir)  # 「学过」標記をすぐ反映
                        return self._json(lesson_view(news_dir, USER_DIR, data.get("date"), today))
                    if path == "/api/grammar/review":
                        review_point(prog, str(data.get("id", "")), int(data.get("grade", 0)), today)
                        save_progress(prog_path, prog)
                        return self._json({"ok": True})
            except ValueError as e:  # 進度ファイル破損など
                print(f"{datetime.now(JST):%H:%M:%S} {path} 出错：{e}", file=sys.stderr, flush=True)
                return self._json({"error": str(e)}, 500)
            return self._json({"error": "not found"}, 404)

        def do_DELETE(self):
            u = urllib.parse.urlparse(self.path)
            word = urllib.parse.parse_qs(u.query).get("word", [""])[0]
            if u.path != "/api/vocab" or not word:
                return self._json({"error": "not found"}, 404)
            with _vocab_lock:
                words, ignored = vocab_load(vocab_path)
                words = [w for w in words if w["word"] != word]
                ignored.add(word)  # 自動追加で復活しないように記録
                vocab_save(vocab_path, words, ignored)
            return self._json({"ok": True, "count": len(words)})

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"ローカルサーバー起動: http://localhost:{port}/  （単語帳: http://localhost:{port}/vocab.html、Ctrl+C で終了）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


VOCAB_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>日语单词本</title>
<style>
/*__BASE_CSS__*/
.tabs { display: flex; gap: 8px; margin: 24px 0 20px; flex-wrap: wrap; align-items: center; }
.tabs .meta { margin-left: auto; color: var(--muted); font-size: 13px; }
.flash { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; padding: 32px 24px;
  text-align: center; max-width: 640px; margin: 0 auto; }
.flash .progress { color: var(--muted); font-size: 12px; margin-bottom: 18px; }
.flash .front { font-size: 44px; font-weight: 700; line-height: 1.3; word-break: break-all; }
.flash .kana { font-size: 20px; color: var(--evt); margin-top: 8px; }
.flash .zh { font-size: 20px; margin-top: 10px; font-weight: 600; }
.flash .sent { text-align: left; background: var(--chip); border-radius: 10px; padding: 12px 14px; margin-top: 20px;
  line-height: 2.1; font-size: 15px; }
.flash .sent .szh { line-height: 1.6; color: var(--muted); font-size: 13px; margin-top: 4px; }
.flash .src { font-size: 12px; color: var(--muted); margin-top: 10px; text-align: left; }
.flash .src a { color: var(--muted); }
.flash .actions { display: flex; justify-content: center; gap: 10px; margin-top: 24px; flex-wrap: wrap; }
.flash .actions .btn { min-width: 96px; }
b.hit { background: #f3d36b; color: #1d1d1b; border-radius: 2px; font-weight: inherit; }
.say { border: 0; background: none; cursor: pointer; font-size: 20px; vertical-align: middle; color: var(--muted); }
.say:hover { color: var(--text); }
.list { display: grid; gap: 10px; }
.item { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px;
  display: grid; grid-template-columns: minmax(120px, 200px) 1fr auto; gap: 14px; align-items: start; }
.item .w { font-size: 22px; font-weight: 700; line-height: 1.2; word-break: break-all; }
.item .k { color: var(--evt); font-size: 14px; margin-top: 4px; }
.item .z { font-weight: 600; }
.item .s { font-size: 13px; color: var(--muted); margin-top: 4px; }
.item .lv { font-size: 12px; color: var(--muted); margin-top: 6px; }
.item .ops { display: flex; flex-direction: column; gap: 6px; align-items: flex-end; }
.dots { letter-spacing: 2px; color: var(--evt); }
.lvbar { display: flex; gap: 6px; flex-wrap: wrap; margin: -8px 0 20px; }
.lvbar .chip { font-size: 12px; padding: 4px 12px; }
.flash .pos { font-size: 12px; color: var(--muted); margin-left: 6px; }
.flash .sent .lbl { font-size: 11px; color: var(--muted); line-height: 1.4; }
.auto { font-size: 11px; color: var(--muted); border: 1px solid var(--line); border-radius: 6px; padding: 0 5px; margin-left: 4px; }
@media (max-width: 720px) {
  .item { grid-template-columns: 1fr; }
  .item .ops { flex-direction: row; justify-content: flex-end; }
  .flash .front { font-size: 34px; }
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>日语单词本</h1>
      <div class="sub">数据保存在 news/vocab.md ・ 按遗忘曲线安排复习 ・ 每次抓取新闻时自动加入其中的 N5、N4 单词</div>
    </div>
    <a class="navlink" href="index.html">← 返回新闻</a>
  </header>
  <div class="notice" id="fileNotice" hidden>单词本需要本地服务读取 vocab.md，请运行
    <code>python3 scripts/fetch_news.py --serve</code>，然后访问 <code>http://localhost:8765/vocab.html</code></div>

  <div class="tabs">
    <button class="chip" data-tab="review" aria-pressed="true">今日复习</button>
    <button class="chip" data-tab="list" aria-pressed="false">全部单词</button>
    <span class="meta" id="meta"></span>
  </div>
  <div class="lvbar" id="lvbar"></div>

  <main id="view"></main>
</div>
<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = (path, opt) => fetch(path, opt).then(r => r.ok ? r.json() : r.text().then(t => Promise.reject(new Error(t))));
const toast = msg => { const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 2000); };
const LEVELS = 7;
let words = [], today = "", tab = "review", queue = [], pos = 0, revealed = false, lv = "all";
const LV = ["N5", "N4", "N3", "N2", "N1", "级外"];
try { lv = localStorage.getItem("vocab-lv") || "all"; } catch (e) {}
const inLv = w => lv === "all" || (w.jlpt || "级外") === lv;
const badge = w => `<span class="lvb ${esc(w.jlpt || "")}">${esc(w.jlpt || "级外")}</span>`;

function say(text) {
  if (!("speechSynthesis" in window)) return toast("当前浏览器不支持朗读");
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text); u.lang = "ja-JP"; u.rate = 0.85;
  speechSynthesis.speak(u);
}
// 例句：带注音，并高亮当前单词
function sentenceHtml(w, key = "sentence") {
  const segs = w[key + "_ruby"] && w[key + "_ruby"].length ? w[key + "_ruby"] : [[w[key] || "", ""]];
  const full = segs.map(s => s[0]).join(""), at = full.indexOf(w.word), end = at + w.word.length;
  let i = 0;
  return segs.map(([t, r]) => {
    const a = i; i += t.length;
    if (at < 0 || a >= end || i <= at) return r ? `<ruby>${esc(t)}<rt>${esc(r)}</rt></ruby>` : esc(t);
    if (r) return `<b class="hit"><ruby>${esc(t)}<rt>${esc(r)}</rt></ruby></b>`;
    const s0 = Math.max(at - a, 0), s1 = Math.min(end - a, t.length);  // 假名段只高亮重叠部分
    return esc(t.slice(0, s0)) + `<b class="hit">${esc(t.slice(s0, s1))}</b>` + esc(t.slice(s1));
  }).join("");
}
const dots = lv => "●".repeat(lv) + "○".repeat(Math.max(0, LEVELS - lv));

async function load() {
  const d = await api("/api/vocab");
  words = d.words; today = d.today;
  const scope = words.filter(inLv), due = scope.filter(w => !w.due || w.due <= today);
  $("#meta").textContent = `${lv === "all" ? "共" : lv + "："} ${scope.length} 个单词 ・ 今日待复习 ${due.length} 个`;
  const cnt = k => words.filter(w => (w.jlpt || "级外") === k).length;
  $("#lvbar").innerHTML = `<button class="chip" data-lv="all" aria-pressed="${lv === "all"}">全部等级 ${words.length}</button>` +
    LV.filter(k => cnt(k)).map(k => `<button class="chip" data-lv="${k}" aria-pressed="${lv === k}">${k} ${cnt(k)}</button>`).join("");
}
function startReview(all) {
  const due = words.filter(w => inLv(w) && (all || !w.due || w.due <= today));
  queue = due.sort(() => Math.random() - 0.5); pos = 0; revealed = false;
  render();
}

function renderReview() {
  if (!words.length) return `<div class="empty">单词本还是空的。去 <a href="index.html">新闻页面</a> 选中日文词语，点「加入单词本」吧。</div>`;
  if (pos >= queue.length) return `<div class="flash"><div class="front" style="font-size:28px">🎉 今天的复习完成了</div>
      <div class="actions"><button class="btn" id="again">再随机复习全部单词</button></div></div>`;
  const w = queue[pos];
  return `<div class="flash">
    <div class="progress">${pos + 1} / ${queue.length} ・ 熟练度 <span class="dots">${dots(w.level)}</span></div>
    <div>${badge(w)}${w.origin === "自动" ? '<span class="auto">自动</span>' : ""}</div>
    <div class="front" lang="ja">${esc(w.word)} <button class="say" data-say="${esc(w.kana || w.word)}" title="朗读">🔊</button></div>
    ${revealed ? `
      <div class="kana" lang="ja">${esc(w.kana)}${w.pos ? `<span class="pos">${esc(w.pos)}</span>` : ""}</div>
      <div class="zh">${esc(w.zh) || "（未填写释义）"}</div>
      ${w.ex ? `<div class="sent"><div class="lbl">词典例句</div><div lang="ja">${sentenceHtml(w, "ex")}
        <button class="say" data-say="${esc(w.ex)}" title="朗读例句">🔊</button></div>
        ${w.ex_zh ? `<div class="szh">${esc(w.ex_zh)}</div>` : ""}</div>` : ""}
      ${w.sentence ? `<div class="sent"><div class="lbl">新闻原句</div><div lang="ja">${sentenceHtml(w)}
        <button class="say" data-say="${esc(w.sentence)}" title="朗读例句">🔊</button></div>
        ${w.sentence_zh ? `<div class="szh">${esc(w.sentence_zh)}</div>` : ""}</div>` : ""}
      ${w.source ? `<div class="src">出处：${w.link ? `<a href="${esc(w.link)}" target="_blank" rel="noopener">${esc(w.source)}</a>` : esc(w.source)}</div>` : ""}
      <div class="actions">
        <button class="btn" data-grade="0">不认识</button>
        <button class="btn" data-grade="1">模糊</button>
        <button class="btn primary" data-grade="2">认识</button>
      </div>` : `
      <div class="actions"><button class="btn primary" id="reveal">显示答案（空格键）</button></div>`}
  </div>`;
}

function renderList() {
  if (!words.length) return `<div class="empty">单词本还是空的。</div>`;
  const order = w => LV.indexOf(w.jlpt || "级外");
  const sorted = words.filter(inLv).sort((a, b) => order(a) - order(b) || (b.added || "").localeCompare(a.added || ""));
  if (!sorted.length) return `<div class="empty">这个等级还没有单词</div>`;
  return `<div class="list">${sorted.map(w => `
    <div class="item">
      <div><div class="w" lang="ja">${esc(w.word)}</div><div class="k" lang="ja">${esc(w.kana)}</div>
        <div style="margin-top:6px">${badge(w)}${w.origin === "自动" ? '<span class="auto">自动</span>' : ""}</div></div>
      <div><div class="z">${esc(w.zh) || "（未填写释义）"}</div>
        ${w.sentence ? `<div class="s" lang="ja">${esc(w.sentence)}</div>` : ""}
        <div class="lv">熟练度 <span class="dots">${dots(w.level)}</span> ・ 下次复习 ${esc(w.due || "-")} ・ 添加于 ${esc(w.added || "-")}</div></div>
      <div class="ops">
        <button class="say" data-say="${esc(w.kana || w.word)}" title="朗读">🔊</button>
        <button class="btn danger" data-del="${esc(w.word)}">删除</button>
      </div>
    </div>`).join("")}</div>`;
}

function render() {
  $("#view").innerHTML = tab === "review" ? renderReview() : renderList();
}

$("#view").onclick = async e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.say) return say(b.dataset.say);
  if (b.id === "reveal") { revealed = true; return render(); }
  if (b.id === "again") return startReview(true);
  if (b.dataset.grade) {
    const w = queue[pos], grade = +b.dataset.grade;
    try { await api("/api/review", { method: "POST", body: JSON.stringify({ word: w.word, grade }) }); }
    catch (err) { return toast("保存失败：" + err.message); }
    if (grade === 0) queue.push(w);  // 不认识的词本轮稍后再出现一次
    pos++; revealed = false;
    await load(); render(); return;
  }
  if (b.dataset.del) {
    if (!confirm(`从单词本中删除「${b.dataset.del}」？\n（删除后，以后抓取新闻时也不会再自动加入这个词）`)) return;
    try { await api("/api/vocab?word=" + encodeURIComponent(b.dataset.del), { method: "DELETE" }); }
    catch (err) { return toast("删除失败：" + err.message); }
    toast(`已删除「${b.dataset.del}」`);
    queue = queue.filter(w => w.word !== b.dataset.del);
    await load(); render();
  }
};
$("#lvbar").onclick = async e => {
  const b = e.target.closest("[data-lv]"); if (!b) return;
  lv = b.dataset.lv; try { localStorage.setItem("vocab-lv", lv); } catch (err) {}
  await load(); startReview(false);
};
document.querySelectorAll(".tabs .chip").forEach(b => b.onclick = () => {
  tab = b.dataset.tab;
  document.querySelectorAll(".tabs .chip").forEach(c => c.setAttribute("aria-pressed", c === b));
  render();
});
document.addEventListener("keydown", e => {
  if (tab !== "review" || e.target.closest("input, textarea")) return;
  if (e.code === "Space" && !revealed && pos < queue.length) { e.preventDefault(); revealed = true; render(); }
  else if (revealed && ["Digit1", "Digit2", "Digit3"].includes(e.code))
    document.querySelector(`[data-grade="${e.code.slice(-1) - 1}"]`)?.click();
});

if (!location.protocol.startsWith("http")) {
  $("#fileNotice").hidden = false;
  $("#view").innerHTML = `<div class="empty">请通过本地服务打开此页面</div>`;
} else {
  load().then(() => startReview(false)).catch(e => $("#view").innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`);
}
</script>
</body>
</html>
"""


# ==========================================================================
# エントリポイント
# ==========================================================================

def run_fetch(news_dir, log=print):
    """収集 → 精選 → 付加 → 保存 → 単語帳自動追加 → ページ再生成。結果の要約を返す。"""
    now = datetime.now(JST)
    since = datetime.combine(now.date(), datetime.min.time(), JST) - timedelta(hours=6)  # 前日18時以降
    log("① 正在从 Google 新闻抓取候选…")
    raw = collect(since)
    seen = seen_titles(news_dir, f"{now:%Y-%m-%d}")
    for x in raw:
        x["stale"] = stale_reason(x, now.date(), seen)
    n_stale = sum(1 for x in raw if x["stale"])
    log(f"   排除疑似旧文章 {n_stale} 条（Google 的时间是重新收录时间，不是发布时间）")

    picked = []
    for key, heading, limit in SECTIONS:
        items = sorted((dict(x, section=key) for x in raw if not x["stale"] and classify(x) == key),
                       key=lambda x: x["published"], reverse=True)
        picked += [{k: v for k, v in x.items() if k not in ("stale", "gallery")} for x in items[:limit]]
    log(f"② 候选 {len(raw)} 条 → 精选 {len(picked)} 条；正在生成翻译、注音和逐词拆解（新词较多时需要几分钟）…")
    enrich(picked, news_dir / "translations_zh.json")

    outdir = news_dir / f"{now:%Y-%m-%d}"
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"tokyo_news_{now:%Y-%m-%d}"
    (outdir / "raw_candidates.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1))
    (outdir / f"{stem}.json").write_text(json.dumps(picked, ensure_ascii=False, indent=1))
    (outdir / f"{stem}.md").write_text(render(picked, now, since))
    n_auto = auto_add_vocab(picked, VOCAB_PATH)
    log(f"③ 已保存 {outdir / (stem + '.md')}；单词本自动加入 {'/'.join(AUTO_LEVELS)} 单词 {n_auto} 个")
    try:
        les = generate_today(news_dir, USER_DIR, now.date(), vocab_load(VOCAB_PATH)[0], ruby_fn=furigana, log=log,
                             lock=_vocab_lock)
        if les:
            log(f"   今日一课：{les['main']['title']}")
    except Exception as e:  # 課程の失敗でニュースの出力を止めない
        log(f"   今日一课生成失败：{type(e).__name__}: {e}")
    page, n = build(news_dir)
    log(f"④ 页面已更新（共 {n} 天）")
    return {"date": f"{now:%Y-%m-%d}", "candidates": len(raw), "picked": len(picked), "vocab_added": n_auto}


def _importable(name):
    import importlib.util
    return importlib.util.find_spec(name) is not None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "news"), help="出力先ルートディレクトリ")
    ap.add_argument("--page-only", action="store_true", help="収集せずページだけ再生成する")
    ap.add_argument("--serve", action="store_true", help="ページを再生成してローカルサーバーを起動する（収集はしない）")
    ap.add_argument("--port", type=int, default=8765, help="--serve のポート番号")
    args = ap.parse_args()
    news_dir = Path(args.out)
    news_dir.mkdir(parents=True, exist_ok=True)
    migrate_user_data(news_dir, USER_DIR)

    missing = [m for m in ("pykakasi", "janome") if not _importable(m)]
    if missing:  # 無いと注音・逐語解析が黙って空になるので、ここで止める
        sys.exit(f"缺少依赖 {', '.join(missing)}（当前 Python：{sys.executable}）\n"
                 f"请运行：{sys.executable} -m pip install {' '.join(missing)}\n"
                 f"或改用已安装依赖的 Python，例如：/opt/miniconda3/bin/python3 scripts/fetch_news.py")

    if not (args.page_only or args.serve):
        run_fetch(news_dir)
    else:
        print("正在生成页面（新标题需要补全翻译和讲解时，可能要几分钟）…", file=sys.stderr, flush=True)
        page, n = build(news_dir)
        print(f"ページ更新（{n} 日分）: {page}")
    if args.serve:
        serve(news_dir, args.port)


if __name__ == "__main__":
    main()
