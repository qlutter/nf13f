import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from sec_api import Form13FHoldingsApi


NPS_CIK = os.getenv("NPS_CIK", "1608046")
TOP_N = int(os.getenv("TOP_N", "30"))
QUERY_SIZE = int(os.getenv("QUERY_SIZE", "8"))
INCLUDE_OPTIONS = os.getenv("INCLUDE_OPTIONS", "false").lower() == "true"
OUT_DIR = os.getenv("OUT_DIR", "out")


def die(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    sys.exit(1)


def to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def fetch_recent_13f_filings(api_key: str) -> List[Dict[str, Any]]:
    api = Form13FHoldingsApi(api_key=api_key)

    # 무료 100콜 절약 목적:
    # 한 번의 검색으로 최근 여러 개 13F holdings records를 받고,
    # 로컬에서 최신 periodOfReport / 직전 periodOfReport를 고릅니다.
    search_params = {
        "query": f"cik:{NPS_CIK}",
        "from": "0",
        "size": str(QUERY_SIZE),
        "sort": [{"filedAt": {"order": "desc"}}],
    }

    response = api.get_data(search_params)
    filings = response.get("data", [])

    if not filings:
        die("sec-api 응답에 13F holdings 데이터가 없습니다. API 키, CIK, 플랜 권한을 확인하세요.")

    return filings


def pick_latest_two_unique_periods(filings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # filedAt 기준 최신 제출물이 아니라, 실제 보고 기준일 periodOfReport 기준 최신/직전 분기를 선택합니다.
    # 같은 분기에 수정 보고가 여러 개 있으면 filedAt 최신 것을 사용합니다.
    filings_sorted = sorted(
        filings,
        key=lambda f: (clean_str(f.get("periodOfReport")), clean_str(f.get("filedAt"))),
        reverse=True,
    )

    selected = []
    seen_periods = set()

    for filing in filings_sorted:
        period = clean_str(filing.get("periodOfReport"))
        if not period:
            continue
        if period in seen_periods:
            continue
        selected.append(filing)
        seen_periods.add(period)
        if len(selected) == 2:
            break

    if len(selected) < 2:
        die("비교할 13F 분기 데이터가 2개 미만입니다. QUERY_SIZE를 늘려보세요.")

    return selected


def normalize_holdings(filing: Dict[str, Any]) -> pd.DataFrame:
    holdings = filing.get("holdings", [])
    rows = []

    for h in holdings:
        put_call = clean_str(h.get("putCall")).upper()

        # 미국 주식/ETF 보유 변화 중심 테스트이므로 옵션 포지션은 기본 제외
        if put_call and not INCLUDE_OPTIONS:
            continue

        ticker = clean_str(h.get("ticker")).upper()
        cusip = clean_str(h.get("cusip")).upper()
        name = clean_str(h.get("nameOfIssuer"))
        title = clean_str(h.get("titleOfClass"))
        value = to_float(h.get("value"))

        shrs = h.get("shrsOrPrnAmt", {}) or {}
        shares = to_float(shrs.get("sshPrnamt"))
        shares_type = clean_str(shrs.get("sshPrnamtType"))

        # ticker가 있으면 ticker 기준으로 합산, 없으면 CUSIP 기준으로 추적
        key = ticker if ticker else f"CUSIP:{cusip}"

        if not key or key == "CUSIP:":
            continue

        rows.append(
            {
                "key": key,
                "ticker": ticker,
                "cusip": cusip,
                "name": name,
                "titleOfClass": title,
                "putCall": put_call,
                "shares": shares,
                "sharesType": shares_type,
                "value": value,
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "key",
                "ticker",
                "cusip",
                "name",
                "titleOfClass",
                "putCall",
                "shares",
                "sharesType",
                "value",
            ]
        )

    df = pd.DataFrame(rows)

    # 동일 ticker가 여러 CUSIP/class로 나뉘는 경우를 단순 합산
    grouped = (
        df.groupby("key", as_index=False)
        .agg(
            ticker=("ticker", "first"),
            cusip=("cusip", "first"),
            name=("name", "first"),
            titleOfClass=("titleOfClass", lambda x: ", ".join(sorted(set(v for v in x if v)))),
            putCall=("putCall", lambda x: ", ".join(sorted(set(v for v in x if v)))),
            shares=("shares", "sum"),
            value=("value", "sum"),
        )
    )

    return grouped


def compare_holdings(latest_df: pd.DataFrame, previous_df: pd.DataFrame) -> pd.DataFrame:
    merged = latest_df.merge(
        previous_df,
        on="key",
        how="outer",
        suffixes=("_latest", "_previous"),
    )

    for col in ["shares_latest", "shares_previous", "value_latest", "value_previous"]:
        merged[col] = merged[col].fillna(0.0)

    def choose_text(row: pd.Series, base: str) -> str:
        latest = clean_str(row.get(f"{base}_latest"))
        previous = clean_str(row.get(f"{base}_previous"))
        return latest or previous

    records = []
    total_latest_value = merged["value_latest"].sum()
    total_previous_value = merged["value_previous"].sum()

    for _, row in merged.iterrows():
        latest_shares = to_float(row["shares_latest"])
        previous_shares = to_float(row["shares_previous"])
        latest_value = to_float(row["value_latest"])
        previous_value = to_float(row["value_previous"])

        shares_change = latest_shares - previous_shares
        value_change = latest_value - previous_value

        if previous_shares == 0 and latest_shares > 0:
            status = "신규편입"
        elif previous_shares > 0 and latest_shares == 0:
            status = "전량매도_또는_보고제외"
        elif shares_change > 0:
            status = "보유수량증가"
        elif shares_change < 0:
            status = "보유수량감소"
        else:
            status = "보유수량동일"

        shares_change_pct: Optional[float]
        if previous_shares > 0:
            shares_change_pct = shares_change / previous_shares * 100
        else:
            shares_change_pct = None

        records.append(
            {
                "status": status,
                "key": row["key"],
                "ticker": choose_text(row, "ticker"),
                "name": choose_text(row, "name"),
                "cusip": choose_text(row, "cusip"),
                "titleOfClass": choose_text(row, "titleOfClass"),
                "shares_latest": latest_shares,
                "shares_previous": previous_shares,
                "shares_change": shares_change,
                "shares_change_pct": shares_change_pct,
                "value_latest": latest_value,
                "value_previous": previous_value,
                "value_change": value_change,
                "weight_latest_pct": latest_value / total_latest_value * 100 if total_latest_value else 0,
                "weight_previous_pct": previous_value / total_previous_value * 100 if total_previous_value else 0,
            }
        )

    result = pd.DataFrame(records)
    result["abs_value_change"] = result["value_change"].abs()
    result = result.sort_values(["abs_value_change"], ascending=False).drop(columns=["abs_value_change"])

    return result


def money_fmt(x: float) -> str:
    return f"${x:,.0f}"


def pct_fmt(x: Any) -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{float(x):,.2f}%"


def make_table(df: pd.DataFrame, cols: List[str], top_n: int) -> str:
    if df.empty:
        return "_없음_"

    view = df.head(top_n).copy()

    for c in ["value_latest", "value_previous", "value_change"]:
        if c in view.columns:
            view[c] = view[c].map(money_fmt)

    for c in ["shares_change_pct", "weight_latest_pct", "weight_previous_pct"]:
        if c in view.columns:
            view[c] = view[c].map(pct_fmt)

    for c in ["shares_latest", "shares_previous", "shares_change"]:
        if c in view.columns:
            view[c] = view[c].map(lambda x: f"{x:,.0f}")

    return view[cols].to_markdown(index=False)


def write_report(
    latest_filing: Dict[str, Any],
    previous_filing: Dict[str, Any],
    diff_df: pd.DataFrame,
    latest_df: pd.DataFrame,
    previous_df: pd.DataFrame,
) -> str:
    latest_period = latest_filing.get("periodOfReport")
    previous_period = previous_filing.get("periodOfReport")

    total_latest = latest_df["value"].sum()
    total_previous = previous_df["value"].sum()

    new_df = diff_df[diff_df["status"] == "신규편입"].sort_values("value_latest", ascending=False)
    increased_df = diff_df[diff_df["status"] == "보유수량증가"].sort_values("value_change", ascending=False)
    decreased_df = diff_df[diff_df["status"] == "보유수량감소"].sort_values("value_change", ascending=True)
    sold_df = diff_df[diff_df["status"] == "전량매도_또는_보고제외"].sort_values("value_previous", ascending=False)

    common_cols = [
        "ticker",
        "name",
        "shares_latest",
        "shares_previous",
        "shares_change",
        "shares_change_pct",
        "value_latest",
        "value_previous",
        "value_change",
        "weight_latest_pct",
    ]

    report = f"""# 국민연금 미국 13F 포트폴리오 변화 리포트

생성시각: {datetime.now(timezone.utc).isoformat()}

## 기준 정보

| 항목 | 최신 분기 | 직전 분기 |
|---|---:|---:|
| periodOfReport | {latest_period} | {previous_period} |
| filedAt | {latest_filing.get("filedAt")} | {previous_filing.get("filedAt")} |
| accessionNo | {latest_filing.get("accessionNo")} | {previous_filing.get("accessionNo")} |
| 총 보유 종목 수 | {len(latest_df):,} | {len(previous_df):,} |
| 총 보고가치 | {money_fmt(total_latest)} | {money_fmt(total_previous)} |

> 주의: 13F는 분기말 보유 현황입니다. 실제 매수/매도 체결일은 알 수 없습니다.  
> 따라서 여기서의 “매수/감소”는 최신 분기말 보유수량과 직전 분기말 보유수량의 차이입니다.

## 신규 편입 Top {TOP_N}

{make_table(new_df, common_cols, TOP_N)}

## 보유수량 증가 Top {TOP_N}

{make_table(increased_df, common_cols, TOP_N)}

## 보유수량 감소 Top {TOP_N}

{make_table(decreased_df, common_cols, TOP_N)}

## 전량매도 또는 13F 보고 제외 Top {TOP_N}

{make_table(sold_df, common_cols, TOP_N)}
"""

    return report


def main() -> None:
    api_key = os.getenv("SEC_API_KEY")
    if not api_key:
        die("환경변수 SEC_API_KEY가 없습니다. GitHub Secrets에 SEC_API_KEY를 등록하세요.")

    os.makedirs(OUT_DIR, exist_ok=True)

    filings = fetch_recent_13f_filings(api_key)
    latest_filing, previous_filing = pick_latest_two_unique_periods(filings)

    latest_df = normalize_holdings(latest_filing)
    previous_df = normalize_holdings(previous_filing)
    diff_df = compare_holdings(latest_df, previous_df)

    latest_period = latest_filing.get("periodOfReport")
    previous_period = previous_filing.get("periodOfReport")

    latest_path = os.path.join(OUT_DIR, f"nps_13f_holdings_{latest_period}.csv")
    previous_path = os.path.join(OUT_DIR, f"nps_13f_holdings_{previous_period}.csv")
    diff_path = os.path.join(OUT_DIR, f"nps_13f_diff_{previous_period}_to_{latest_period}.csv")
    report_path = os.path.join(OUT_DIR, "nps_13f_report.md")

    latest_df.sort_values("value", ascending=False).to_csv(latest_path, index=False)
    previous_df.sort_values("value", ascending=False).to_csv(previous_path, index=False)
    diff_df.to_csv(diff_path, index=False)

    report = write_report(latest_filing, previous_filing, diff_df, latest_df, previous_df)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    # GitHub Actions Summary에 바로 표시
    step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(report)

    print("[OK] 국민연금 13F 비교 완료")
    print(f"Latest period: {latest_period}")
    print(f"Previous period: {previous_period}")
    print(f"Output: {OUT_DIR}")


if __name__ == "__main__":
    main()
