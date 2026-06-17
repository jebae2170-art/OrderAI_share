import React, { useState, useEffect, useMemo } from 'react';
import { useAuth } from '../contexts/AuthContext.jsx';
import { useBrandSeason } from '../contexts/BrandSeasonContext.jsx';
import { createApiClient } from '../service/apiClient.js';
import { Loader2, AlertTriangle, Package, DollarSign, BarChart2, Filter, Info, RefreshCw, HelpCircle, X, CheckCircle, Download, Sparkles, ChevronDown, ChevronUp, TrendingUp, TrendingDown } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, LabelList } from 'recharts';
import { publicUrl } from '../utils/api.js';
import ResetButton from './common/ResetButton.jsx';
import { CHANGED_CELL_CLASS, isChanged } from '../utils/highlight.js';

const formatEok = (amt) => {
  if (amt === 0) return '0';
  return (amt / 100000000).toLocaleString(undefined, { maximumFractionDigits: 0 });
};

const ITEM_CODE_MAP = {
  '니트가디건': 'KC', '니트풀오버': 'KP', '다운베스트': 'DV', '다운점퍼': 'DJ',
  '데님셔츠': 'DR', '맨투맨': 'MT', '반팔카라티셔츠': 'TS', '방풍자켓': 'WJ',
  '스커트': 'SK', '우븐셔츠': 'WS', '우븐팬츠': 'WP', '원피스': 'OP',
  '점퍼': 'JP', '진바지': 'DP', '진스커트': 'DS', '진자켓': 'DK',
  '트레이닝(상의)': 'TR', '패딩': 'PD', '팬츠': 'PT', '폴라폴리스점퍼': 'FD', '후드티': 'HD',
};
const fmtItem = (nm) => {
  const cd = ITEM_CODE_MAP[nm];
  return cd ? `${cd}(${nm})` : nm;
};

const KPICard = ({ label, value, sub, icon: Icon, color }) => (
  <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5 flex flex-col">
    <div className="flex items-center gap-2 mb-2">
      <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${color}`}>
        <Icon className="w-4 h-4 text-white" />
      </div>
      <span className="text-xs text-gray-500">{label}</span>
    </div>
    <p className="text-xl font-bold text-gray-900">{value}</p>
    {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
  </div>
);

export default function OrderSuggest() {
  const { user } = useAuth();
  const { brand, season } = useBrandSeason();
  const api = useMemo(
    () => createApiClient(user?.email, brand, season, user?.role),
    [user?.email, brand, season],
  );
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [showColorGuide, setShowColorGuide] = useState(false);
  const [rescaling, setRescaling] = useState(false);
  const [rescaleMsg, setRescaleMsg] = useState(null);
  const [budgetConfig, setBudgetConfig] = useState(null);
  const [classAnalysis, setClassAnalysis] = useState([]);
  const [prevStyles, setPrevStyles] = useState([]);
  const [itemAnalysis, setItemAnalysis] = useState([]);
  const [expandedCats, setExpandedCats] = useState({});
  const [confirmedQtys, setConfirmedQtys] = useState({});  // { "partCd_colorCd": qty }
  const [confirming, setConfirming] = useState(false);
  const [confirmMsg, setConfirmMsg] = useState(null);
  // 이미지 팝업
  const [imgPopup, setImgPopup] = useState(null); // { url, name, code }
  // Gap 진단
  const [gapReview, setGapReview] = useState(null);
  const [showGapPanel, setShowGapPanel] = useState(false);
  const [gapLoading, setGapLoading] = useState(false);
  const [colorMapping, setColorMapping] = useState(null);

  // 데이터 로드 시 confirmedQtys 초기화
  // 1) confirmed_order_data.json이 있으면 확정수량 로드 + confirmMsg 복원
  // 2) 없으면 AI추천수량(c.qty) 디폴트
  const initConfirmedQtys = async (recData) => {
    // 먼저 AI추천수량으로 디폴트 세팅
    const defaultQtys = {};
    for (const rec of recData.recommendations) {
      const colors = (rec.colors || []).filter(c => c.color_cd && c.ratio > 0);
      if (colors.length > 0) {
        for (const c of colors) {
          defaultQtys[`${rec.new_part_cd}_${c.color_cd}`] = c.qty || 0;
        }
      } else {
        defaultQtys[`${rec.new_part_cd}_`] = rec.추천발주량 || 0;
      }
    }

    // 저장된 확정수량 로드 시도
    try {
      const saved = await api.fetchFile('confirmed_order_data.json');
      if (saved) {
        if (saved.orders && saved.orders.length > 0) {
          // 파이프라인 재실행 감지: 추천 데이터가 확정 이후에 갱신되었으면 확정 무효화
          const recAt = recData.metadata?.confirmed_at;
          const savedAt = saved.confirmed_at;
          if (recAt && savedAt && new Date(recAt) > new Date(savedAt)) {
            console.log('[OrderSuggest] 파이프라인 재실행 감지 — 확정수량을 AI추천수량으로 리셋');
            setConfirmedQtys(defaultQtys);
            return;
          }
          const savedQtys = { ...defaultQtys };
          for (const o of saved.orders) {
            const key = `${o.new_part_cd}_${o.color_cd}`;
            if (key in savedQtys) {
              savedQtys[key] = o.confirmed_qty;
            }
          }
          setConfirmedQtys(savedQtys);
          setConfirmMsg({ ok: true, text: `${saved.orders.length}건 확정됨 (${saved.confirmed_at?.slice(0, 10)})` });
          return;
        }
      }
    } catch {}

    setConfirmedQtys(defaultQtys);
  };

  // 확정수량 스타일 합계
  const getStyleConfirmedTotal = (rec) => {
    const colors = rec.colors || [];
    if (colors.length > 0) {
      return colors.reduce((sum, c) => {
        if (!c.color_cd || c.ratio <= 0) return sum;
        return sum + (confirmedQtys[`${rec.new_part_cd}_${c.color_cd}`] || 0);
      }, 0);
    }
    return confirmedQtys[`${rec.new_part_cd}_`] || 0;
  };

  // 수량 확정 API 호출
  const handleConfirmOrders = async () => {
    if (!data) return;
    setConfirming(true);
    setConfirmMsg(null);
    try {
      const orders = [];
      for (const rec of data.recommendations) {
        const colors = rec.colors || [];
        if (colors.length > 0) {
          for (const c of colors) {
            if (!c.color_cd || c.ratio <= 0) continue;
            const qty = confirmedQtys[`${rec.new_part_cd}_${c.color_cd}`] || 0;
            if (qty > 0) {
              const order = {
                class2: rec.class2 || rec.new_class2,
                new_item_nm: rec.new_item_nm || '',
                new_part_cd: rec.new_part_cd,
                color_cd: c.color_cd,
                confirmed_qty: qty,
              };
              if (rec.size_range) order.size_range = rec.size_range;
              orders.push(order);
            }
          }
        } else {
          const qty = confirmedQtys[`${rec.new_part_cd}_`] || 0;
          if (qty > 0) {
            const order = {
              class2: rec.class2 || rec.new_class2,
              new_item_nm: rec.new_item_nm || '',
              new_part_cd: rec.new_part_cd,
              color_cd: '-',
              confirmed_qty: qty,
            };
            if (rec.size_range) order.size_range = rec.size_range;
            if (rec.sex) order.sex = rec.sex;
            orders.push(order);
          }
        }
      }
      const res = await api.post('/api/confirmed-orders', { season: data.metadata.season, orders });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `서버 오류 (${res.status})`);
      }
      setConfirmMsg({ ok: true, text: `${orders.length}건 확정 완료` });
    } catch (err) {
      setConfirmMsg({ ok: false, text: err.message });
    } finally {
      setConfirming(false);
    }
  };

  // Excel Export
  const handleExcelExport = async () => {
    if (!data) return;
    const XLSX = await import('xlsx');
    const rows = [];
    for (const rec of data.recommendations) {
      const colors = rec.colors || [];
      if (colors.length > 0) {
        for (const c of colors) {
          if (!c.color_cd || c.ratio <= 0) continue;
          rows.push({
            '복종': rec.class2 || rec.new_class2,
            '스타일코드': rec.new_part_cd,
            '아이템': rec.new_item_nm || '',
            '컬러코드': c.color_cd,
            'AI추천수량': c.qty,
            '확정수량': confirmedQtys[`${rec.new_part_cd}_${c.color_cd}`] || 0,
          });
        }
      } else {
        rows.push({
          '복종': rec.class2 || rec.new_class2,
          '스타일코드': rec.new_part_cd,
          '아이템': rec.new_item_nm || '',
          '컬러코드': '-',
          'AI추천수량': rec.추천발주량 || 0,
          '확정수량': confirmedQtys[`${rec.new_part_cd}_`] || 0,
        });
      }
    }
    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, '확정발주');
    XLSX.writeFile(wb, `${data.metadata.season || '26F'}_확정발주.xlsx`);
  };

  const handleRescale = async () => {
    setRescaling(true);
    setRescaleMsg(null);
    try {
      const res = await api.post('/api/rescale-budget', {});
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `서버 오류 (${res.status})`);
      }
      // 재로드
      const [freshData, bc] = await Promise.all([
        api.fetchFile('order_recommendation_data.json'),
        api.fetchFile('budget_config.json'),
      ]);
      if (freshData) {
        setData(freshData);
        initConfirmedQtys(freshData);
        setIsPreview(false);
      }
      setBudgetConfig(bc);
      setRescaleMsg({ ok: true, text: '예산 재적용 완료' });
    } catch (err) {
      setRescaleMsg({ ok: false, text: err.message });
    } finally {
      setRescaling(false);
    }
  };

  useEffect(() => {
    // 예산 config + 전년 실적 병렬 로드
    api.fetchFile('budget_config.json')
      .then(json => setBudgetConfig(json))
      .catch(() => setBudgetConfig(null));

    api.fetchFile('color_mapping.json')
      .then(json => setColorMapping(json))
      .catch(() => setColorMapping(null));

    api.fetchFile('season_closing_data.json')
      .then(json => {
        setClassAnalysis(json?.class_analysis || []);
        setItemAnalysis(json?.item_analysis || []);
        const allStyles = [];
        const actions = json?.style_summary?.action_styles || {};
        for (const styles of Object.values(actions)) {
          allStyles.push(...styles);
        }
        setPrevStyles(allStyles);
      })
      .catch(() => { setClassAnalysis([]); setItemAnalysis([]); setPrevStyles([]); });

    // 확정 데이터만 표시. 미확정(Step 3 미확정 → 백엔드 204)이면 data=null → 빈 상태 안내 (프리뷰 미표시)
    api.fetchFile('order_recommendation_data.json')
      .then(json => {
        if (!json) throw new Error('no confirmed');
        setData(json);
        initConfirmedQtys(json);
        setLoading(false);
      })
      .catch(() => {
        setData(null);
        setLoading(false);
      });
  }, [api]);

  const categories = useMemo(() => {
    if (!data) return [];
    // class2(복종: Inner/Bottom/Outer)가 있으면 그 기준, 없으면 new_class2
    const set = new Set(data.recommendations.map(r => r.class2 || r.new_class2));
    return [...set].sort();
  }, [data]);

  const filtered = useMemo(() => {
    if (!data) return [];
    if (categoryFilter === 'all') return data.recommendations;
    return data.recommendations.filter(r => (r.class2 || r.new_class2) === categoryFilter);
  }, [data, categoryFilter]);

  const kpis = useMemo(() => {
    if (!data) return {};
    const recs = data.recommendations;
    const totalQty = data.metadata.total_recommendation_qty || 0;
    const totalAmt = recs.reduce((sum, r) => sum + (r.추천발주량 || 0) * (r.판매가 || 0), 0);
    const scaledCount = data.metadata.scaled_count || 0;
    return { totalQty, totalAmt, scaledCount, totalStyles: data.metadata.total_styles };
  }, [data]);

  // 확정수량 합계 및 금액 (수량확정 후 표시)
  const isConfirmed = confirmMsg?.ok === true;
  const confirmedKpis = useMemo(() => {
    if (!data || !isConfirmed) return null;
    const recs = data.recommendations;
    let totalQty = 0;
    let totalAmt = 0;
    for (const rec of recs) {
      const colors = (rec.colors || []).filter(c => c.color_cd && c.ratio > 0);
      const price = rec.판매가 || 0;
      if (colors.length > 0) {
        for (const c of colors) {
          const qty = confirmedQtys[`${rec.new_part_cd}_${c.color_cd}`] || 0;
          totalQty += qty;
          totalAmt += qty * price;
        }
      } else {
        const qty = confirmedQtys[`${rec.new_part_cd}_`] || 0;
        totalQty += qty;
        totalAmt += qty * price;
      }
    }
    return { totalQty, totalAmt };
  }, [data, isConfirmed, confirmedQtys]);

  // 컬러 매출비중 테이블 (color_mapping.json::class2_sales_share_final 기반)
  const colorShareTable = useMemo(() => {
    const shares = colorMapping?.class2_sales_share_final;
    if (!shares) return null;
    const nameKr = colorMapping.color_to_name_kr || {};
    const cats = Object.keys(shares).filter(k => k !== '_all').sort();
    const colorOrder = Object.keys(nameKr);
    const rows = colorOrder.map(color => {
      const row = { color, name: nameKr[color] || color };
      cats.forEach(cat => { row[cat] = shares[cat]?.[color] ?? 0; });
      row._all = shares._all?.[color] ?? 0;
      return row;
    });
    return { rows, cats };
  }, [colorMapping]);

  // 예산 KPI 요약 테이블 데이터
  const budgetSummary = useMemo(() => {
    if (!budgetConfig || !data) return null;
    const catBudgets = budgetConfig.category_budgets || [];
    if (catBudgets.length === 0) return null;

    // AI발주금액 + 올해 스타일수: recommendations에서 class2별/아이템별 합산
    const aiOrderAmtByClass2 = {};
    const curItemMap = {}; // { class2: { item_nm: { count, aiAmt } } }
    for (const rec of data.recommendations) {
      const cls2 = rec.class2 || rec.new_class2;
      const amt = (rec.추천발주량 || 0) * (rec.판매가 || 0);
      aiOrderAmtByClass2[cls2] = (aiOrderAmtByClass2[cls2] || 0) + amt;
      const item = rec.new_item_nm || '';
      if (!curItemMap[cls2]) curItemMap[cls2] = {};
      if (!curItemMap[cls2][item]) curItemMap[cls2][item] = { count: 0, aiAmt: 0 };
      curItemMap[cls2][item].count += 1;
      curItemMap[cls2][item].aiAmt += amt;
    }

    // 전년 스타일수: 국내 판매 실적이 있는 스타일만 카운팅
    const prevStyByClass2 = {};
    const prevItemStyMap = {}; // { class2: { item_nm: count } }
    for (const s of prevStyles) {
      if ((s.sale_qty || 0) <= 0) continue;
      const cls2 = s.class2 || '';
      prevStyByClass2[cls2] = (prevStyByClass2[cls2] || 0) + 1;
      if (!prevItemStyMap[cls2]) prevItemStyMap[cls2] = {};
      const item = s.item_nm || '';
      prevItemStyMap[cls2][item] = (prevItemStyMap[cls2][item] || 0) + 1;
    }

    // 전년 아이템별 ord_amt (발주금액): item_analysis에서
    const prevItemOrdAmtMap = {}; // { "class2/item_nm": ord_amt }
    for (const ia of itemAnalysis) {
      prevItemOrdAmtMap[`${ia.class2}/${ia.item_nm}`] = ia.ord_amt || 0;
    }

    // 전년발주(ord_amt), 전년판매(sale_amt): season_closing_data → class_analysis
    const prevOrdAmtByClass2 = {};
    const prevSaleAmtByClass2 = {};
    for (const ca of classAnalysis) {
      if (ca.class2) {
        if (ca.ord_amt) prevOrdAmtByClass2[ca.class2] = ca.ord_amt;
        if (ca.sale_amt) prevSaleAmtByClass2[ca.class2] = ca.sale_amt;
      }
    }

    const rows = catBudgets.map(cat => {
      const budgetAmt = cat.budget_amt || 0;
      const aiOrderAmt = aiOrderAmtByClass2[cat.class2] || 0;
      const prevOrdAmt = prevOrdAmtByClass2[cat.class2] || 0;
      const yoy = prevOrdAmt > 0 ? ((aiOrderAmt - prevOrdAmt) / prevOrdAmt * 100) : 0;
      const targetSTR = cat.target_sell_through_rate || 0;
      const targetRevenue = aiOrderAmt * (targetSTR / 100);
      const prevSaleAmt = prevSaleAmtByClass2[cat.class2] || 0;
      const revenueYoy = prevSaleAmt > 0 ? ((targetRevenue - prevSaleAmt) / prevSaleAmt * 100) : 0;

      // 스타일 수
      const pSty = prevStyByClass2[cat.class2] || 0;
      const cSty = Object.values(curItemMap[cat.class2] || {}).reduce((s, v) => s + v.count, 0);

      // 아이템별 하위 데이터
      const allItems = new Set([
        ...Object.keys(prevItemStyMap[cat.class2] || {}),
        ...Object.keys(curItemMap[cat.class2] || {}),
      ]);
      const items = [...allItems].map(item => {
        const pCount = prevItemStyMap[cat.class2]?.[item] || 0;
        const cCount = curItemMap[cat.class2]?.[item]?.count || 0;
        const pAmt = prevItemOrdAmtMap[`${cat.class2}/${item}`] || 0;
        const cAmt = curItemMap[cat.class2]?.[item]?.aiAmt || 0;
        const amtYoy = pAmt > 0 ? ((cAmt - pAmt) / pAmt * 100) : 0;
        return { item, prevSty: pCount, curSty: cCount, prevAmt: pAmt, aiAmt: cAmt, amtYoy };
      }).sort((a, b) => (a.prevAmt - a.aiAmt) - (b.prevAmt - b.aiAmt)); // 발주 감소 큰 순

      return {
        class2: cat.class2,
        budgetAmt,
        aiOrderAmt,
        prevOrdAmt,
        yoy,
        targetSTR,
        targetRevenue,
        prevSaleAmt,
        revenueYoy,
        prevSty: pSty,
        curSty: cSty,
        items,
      };
    });

    // 합계
    const totalBudgetAmt = rows.reduce((s, r) => s + r.budgetAmt, 0);
    const totalAiOrderAmt = rows.reduce((s, r) => s + r.aiOrderAmt, 0);
    const totalPrevOrdAmt = rows.reduce((s, r) => s + r.prevOrdAmt, 0);
    const totalYoy = totalPrevOrdAmt > 0 ? ((totalAiOrderAmt - totalPrevOrdAmt) / totalPrevOrdAmt * 100) : 0;
    const totalTargetRevenue = rows.reduce((s, r) => s + r.targetRevenue, 0);
    const totalPrevSaleAmt = rows.reduce((s, r) => s + r.prevSaleAmt, 0);
    const totalRevenueYoy = totalPrevSaleAmt > 0 ? ((totalTargetRevenue - totalPrevSaleAmt) / totalPrevSaleAmt * 100) : 0;

    const totalPrevSty = rows.reduce((s, r) => s + r.prevSty, 0);
    const totalCurSty = rows.reduce((s, r) => s + r.curSty, 0);

    return { rows, totalBudgetAmt, totalAiOrderAmt, totalPrevOrdAmt, totalYoy, totalTargetRevenue, totalPrevSaleAmt, totalRevenueYoy, totalPrevSty, totalCurSty };
  }, [budgetConfig, data, classAnalysis, prevStyles, itemAnalysis]);

  // Gap 알람 조건: 카테고리 중 |gap%| > 15% 존재 여부
  const gapAlert = useMemo(() => {
    if (!budgetSummary || !data) return null;
    const cats = (data.metadata?.category_budgets || []).map(cb => {
      const gap = cb.budget_qty > 0
        ? ((cb.recommended_qty - cb.budget_qty) / cb.budget_qty * 100)
        : 0;
      return { class2: cb.class2, budget_qty: cb.budget_qty, rec_qty: cb.recommended_qty, gap_pct: gap };
    });
    const alertCats = cats.filter(c => Math.abs(c.gap_pct) > 15);
    if (alertCats.length === 0) return null;
    const totalBudget = cats.reduce((s, c) => s + c.budget_qty, 0);
    const totalRec = cats.reduce((s, c) => s + c.rec_qty, 0);
    const totalGap = totalBudget > 0 ? ((totalRec - totalBudget) / totalBudget * 100) : 0;
    return { cats: alertCats, totalGap, isUnder: totalGap < 0 };
  }, [budgetSummary, data]);

  const handleGapReview = async () => {
    setGapLoading(true);
    try {
      const res = await api.post('/api/gap-review', {});
      if (!res.ok) throw new Error('Gap 진단 실패');
      const result = await res.json();
      setGapReview(result);
      setShowGapPanel(true);
    } catch (err) {
      console.error(err);
    } finally {
      setGapLoading(false);
    }
  };

  // 확정수량 기반 카테고리별 발주금액
  const confirmedAmtByClass2 = useMemo(() => {
    if (!data || !isConfirmed) return null;
    const map = {};
    for (const rec of data.recommendations) {
      const cls2 = rec.class2 || rec.new_class2;
      const price = rec.판매가 || 0;
      const colors = (rec.colors || []).filter(c => c.color_cd && c.ratio > 0);
      let qty = 0;
      if (colors.length > 0) {
        for (const c of colors) {
          qty += confirmedQtys[`${rec.new_part_cd}_${c.color_cd}`] || 0;
        }
      } else {
        qty = confirmedQtys[`${rec.new_part_cd}_`] || 0;
      }
      map[cls2] = (map[cls2] || 0) + qty * price;
    }
    return map;
  }, [data, isConfirmed, confirmedQtys]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96 text-gray-400">
        <Loader2 className="w-6 h-6 animate-spin mr-2" />
        <span>발주 추천 데이터 로딩 중...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-96 text-gray-500 gap-3">
        <AlertTriangle className="w-8 h-8 text-amber-400" />
        <p className="text-sm">{error}</p>
      </div>
    );
  }

  // Step 3 미확정 → 발주추천 없음: 프리뷰 대신 빈 상태 안내
  if (!data) {
    return (
      <div className="flex flex-col items-center justify-center h-96 text-gray-500 gap-3">
        <Info className="w-8 h-8 text-gray-300" />
        <p className="text-sm font-medium text-gray-600">아직 발주추천이 없습니다</p>
        <p className="text-xs text-gray-400">Step 3에서 유사스타일 매핑을 확정하면 발주추천이 생성됩니다.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Lite 전용: 본인 발주 추천 리셋 (화면 워터마크는 제거, Excel에는 백엔드에서 자동 부착됨) */}
      <div className="flex justify-end">
        <ResetButton
          api={api}
          scope="orders"
          label="Reset"
          confirmMessage="본인 발주 추천 변경 내역을 삭제하고 운영팀 디폴트로 되돌립니다. 매핑은 유지됩니다."
          onDone={() => window.location.reload()}
        />
      </div>

      {/* 이미지 팝업 */}
      {imgPopup && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setImgPopup(null)}>
          <div className="bg-white rounded-xl shadow-2xl p-4 max-w-sm" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-3">
              <div>
                <span className="font-mono font-semibold text-sm">{imgPopup.code}</span>
                {imgPopup.name && <span className="block text-xs text-gray-400">{imgPopup.name}</span>}
              </div>
              <button onClick={() => setImgPopup(null)} className="text-gray-400 hover:text-gray-600 text-lg leading-none">&times;</button>
            </div>
            <img src={imgPopup.url} alt={imgPopup.code} className="w-full rounded-lg" onError={e => { e.target.style.display = 'none'; }} />
          </div>
        </div>
      )}
      {/* KPI 카드 */}
      <div className="grid grid-cols-4 gap-4">
        <KPICard
          label="총 AI추천수량"
          value={`${kpis.totalQty?.toLocaleString()}장`}
          sub={`${kpis.totalStyles}개 스타일`}
          icon={Package}
          color="bg-blue-500"
        />
        <KPICard
          label="확정 발주수량"
          value={confirmedKpis ? `${confirmedKpis.totalQty.toLocaleString()}장` : '-'}
          sub={confirmedKpis ? '수량 확정 완료' : '수량 확정 전'}
          icon={CheckCircle}
          color={confirmedKpis ? 'bg-slate-800' : 'bg-gray-300'}
        />
        <KPICard
          label="추정 발주금액"
          value={(() => {
            const amt = confirmedKpis ? confirmedKpis.totalAmt : kpis.totalAmt;
            return amt >= 100000000
              ? `${(amt / 100000000).toFixed(1)}억원`
              : `${Math.round(amt / 10000).toLocaleString()}만원`;
          })()}
          sub={confirmedKpis ? '확정수량 x 판매가' : 'AI추천수량 x 판매가'}
          icon={DollarSign}
          color="bg-emerald-500"
        />
        {(() => {
          const prevTotalOrdAmt = classAnalysis.reduce((s, c) => s + (c.ord_amt || 0), 0);
          const currentAmt = confirmedKpis ? confirmedKpis.totalAmt : kpis.totalAmt;
          const yoy = prevTotalOrdAmt > 0 ? ((currentAmt - prevTotalOrdAmt) / prevTotalOrdAmt) * 100 : 0;
          const yoyText = prevTotalOrdAmt > 0
            ? `전년대비 ${yoy >= 0 ? '+' : ''}${yoy.toFixed(1)}%`
            : '전년 데이터 없음';
          const valueText = prevTotalOrdAmt >= 100000000
            ? `${(prevTotalOrdAmt / 100000000).toFixed(1)}억원`
            : prevTotalOrdAmt > 0
              ? `${Math.round(prevTotalOrdAmt / 10000).toLocaleString()}만원`
              : '-';
          return (
            <KPICard
              label="전년 발주금액"
              value={valueText}
              sub={yoyText}
              icon={yoy >= 0 ? TrendingUp : TrendingDown}
              color={yoy >= 0 ? 'bg-rose-500' : 'bg-violet-500'}
            />
          );
        })()}
      </div>

      {/* 예산 활용률 차트 */}
      {budgetSummary && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
          <div className="flex items-center gap-2 mb-4 relative">
            <h3 className="text-sm font-semibold text-gray-700">예산 활용 현황</h3>
            {budgetConfig && (
              <div className="group relative">
                <HelpCircle size={15} className="text-gray-400 hover:text-blue-500 cursor-help transition-colors" />
                <div className="hidden group-hover:block absolute top-6 left-0 z-30 w-[620px] bg-white border border-gray-200 rounded-xl shadow-xl p-4">
                  <p className="text-xs font-semibold text-gray-600 mb-2">
                    Budget Ceiling 설정 ({budgetConfig.season}) · {budgetConfig.confirmed_at?.slice(0, 10)}
                  </p>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-gray-200 text-gray-500">
                        <th className="py-1.5 text-left font-semibold">카테고리</th>
                        <th className="py-1.5 text-right font-semibold">전년입고</th>
                        <th className="py-1.5 text-right font-semibold">전년매출</th>
                        <th className="py-1.5 text-right font-semibold text-indigo-600">비중(%)</th>
                        <th className="py-1.5 text-right font-semibold text-indigo-600">매출목표</th>
                        <th className="py-1.5 text-right font-semibold text-indigo-600">매출YoY</th>
                        <th className="py-1.5 text-right font-semibold text-indigo-600">판매율(%)</th>
                        <th className="py-1.5 text-right font-semibold text-green-700">발주예산</th>
                        <th className="py-1.5 text-right font-semibold text-green-700">발주YoY</th>
                      </tr>
                    </thead>
                    <tbody className="text-gray-700">
                      {(budgetConfig.category_budgets || []).map(cat => {
                        const ca = classAnalysis.find(c => c.class2 === cat.class2);
                        const prevOrdAmt = ca?.ord_amt || 0;
                        const prevSaleAmt = ca?.sale_amt || 0;
                        const budgetYoy = prevOrdAmt > 0 ? ((cat.budget_amt - prevOrdAmt) / prevOrdAmt * 100) : 0;
                        const salesYoy = prevSaleAmt > 0 && cat.target_revenue ? ((cat.target_revenue - prevSaleAmt) / prevSaleAmt * 100) : 0;
                        return (
                          <tr key={cat.class2} className="border-b border-gray-100">
                            <td className="py-1.5 font-bold">{cat.class2}</td>
                            <td className="py-1.5 text-right tabular-nums text-gray-400">{formatEok(prevOrdAmt)}억</td>
                            <td className="py-1.5 text-right tabular-nums text-gray-400">{formatEok(prevSaleAmt)}억</td>
                            <td className="py-1.5 text-right tabular-nums text-indigo-600">{cat.share_pct ? cat.share_pct.toFixed(1) : '-'}</td>
                            <td className="py-1.5 text-right tabular-nums text-indigo-600">{cat.target_revenue ? formatEok(cat.target_revenue) + '억' : '-'}</td>
                            <td className={`py-1.5 text-right tabular-nums ${salesYoy >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                              {cat.target_revenue ? `${salesYoy > 0 ? '+' : ''}${salesYoy.toFixed(1)}%` : '-'}
                            </td>
                            <td className="py-1.5 text-right tabular-nums text-indigo-600">{cat.target_sell_through_rate || '-'}%</td>
                            <td className="py-1.5 text-right tabular-nums font-bold text-green-700">{formatEok(cat.budget_amt)}억</td>
                            <td className={`py-1.5 text-right tabular-nums ${budgetYoy >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                              {budgetYoy > 0 ? '+' : ''}{budgetYoy.toFixed(1)}%
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                    <tfoot className="border-t border-gray-200 font-bold text-gray-800">
                      <tr>
                        <td className="py-1.5">합계</td>
                        <td className="py-1.5 text-right tabular-nums text-gray-400">
                          {formatEok(classAnalysis.reduce((s, c) => s + (c.ord_amt || 0), 0))}억
                        </td>
                        <td className="py-1.5 text-right tabular-nums text-gray-400">
                          {formatEok(classAnalysis.reduce((s, c) => s + (c.sale_amt || 0), 0))}억
                        </td>
                        <td className="py-1.5 text-right text-indigo-600">100%</td>
                        <td className="py-1.5 text-right tabular-nums text-indigo-600">
                          {budgetConfig.target_total_revenue ? formatEok(budgetConfig.target_total_revenue) + '억' : '-'}
                        </td>
                        {(() => {
                          const totalPrevSale = classAnalysis.reduce((s, c) => s + (c.sale_amt || 0), 0);
                          const totalSalesYoy = totalPrevSale > 0 && budgetConfig.target_total_revenue
                            ? ((budgetConfig.target_total_revenue - totalPrevSale) / totalPrevSale * 100) : 0;
                          return (
                            <td className={`py-1.5 text-right tabular-nums ${totalSalesYoy >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                              {budgetConfig.target_total_revenue ? `${totalSalesYoy > 0 ? '+' : ''}${totalSalesYoy.toFixed(1)}%` : '-'}
                            </td>
                          );
                        })()}
                        <td className="py-1.5 text-right text-gray-400">-</td>
                        <td className="py-1.5 text-right tabular-nums text-green-700">{formatEok(budgetConfig.total_budget_amt)}억</td>
                        <td className="py-1.5 text-right text-gray-400">-</td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              </div>
            )}
          </div>
          <div className="flex items-center gap-6">
            {/* Donut: 전체 활용률 */}
            <div className="shrink-0 w-[180px]">
              <ResponsiveContainer width="100%" height={180}>
                <PieChart>
                  <Pie
                    data={(() => {
                      const orderAmt = confirmedAmtByClass2
                        ? Object.values(confirmedAmtByClass2).reduce((s, v) => s + v, 0)
                        : budgetSummary.totalAiOrderAmt;
                      return [
                        { name: confirmedAmtByClass2 ? '확정발주' : 'AI발주', value: orderAmt },
                        { name: '미활용', value: Math.max(0, budgetSummary.totalBudgetAmt - orderAmt) },
                      ];
                    })()}
                    cx="50%" cy="50%"
                    innerRadius={55} outerRadius={75}
                    startAngle={90} endAngle={-270}
                    dataKey="value"
                    strokeWidth={0}
                  >
                    <Cell fill={confirmedAmtByClass2 ? '#1e293b' : '#6366f1'} />
                    <Cell fill="#e5e7eb" />
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="text-center -mt-[108px] mb-[50px]">
                {(() => {
                  const orderAmt = confirmedAmtByClass2
                    ? Object.values(confirmedAmtByClass2).reduce((s, v) => s + v, 0)
                    : budgetSummary.totalAiOrderAmt;
                  return (
                    <>
                      <p className="text-2xl font-bold text-gray-900">
                        {budgetSummary.totalBudgetAmt > 0
                          ? `${(orderAmt / budgetSummary.totalBudgetAmt * 100).toFixed(1)}%`
                          : '-'}
                      </p>
                      <p className="text-[11px] text-gray-400 mt-0.5">
                        {formatEok(orderAmt)} / {formatEok(budgetSummary.totalBudgetAmt)}억
                      </p>
                    </>
                  );
                })()}
              </div>
            </div>

            {/* Grouped Bar: 카테고리별 비교 */}
            <div className="flex-1 min-w-0">
              <ResponsiveContainer width="100%" height={240}>
                <BarChart
                  margin={{ top: 20, right: 10, left: 0, bottom: 0 }}
                  data={[...budgetSummary.rows].sort((a, b) => b.budgetAmt - a.budgetAmt).map(r => {
                    const budgetYoy = r.prevOrdAmt > 0 ? ((r.budgetAmt - r.prevOrdAmt) / r.prevOrdAmt * 100) : 0;
                    const cAmt = confirmedAmtByClass2?.[r.class2] || 0;
                    const confirmedYoy = r.prevOrdAmt > 0 ? ((cAmt - r.prevOrdAmt) / r.prevOrdAmt * 100) : 0;
                    return {
                      name: r.class2,
                      예산: Math.round(r.budgetAmt / 100000000),
                      AI발주: Math.round(r.aiOrderAmt / 100000000),
                      ...(confirmedAmtByClass2 ? { 확정발주: Math.round(cAmt / 100000000), 확정YoY: confirmedYoy } : {}),
                      전년발주: Math.round(r.prevOrdAmt / 100000000),
                      판매목표: Math.round(r.targetRevenue / 100000000),
                      전년판매: Math.round(r.prevSaleAmt / 100000000),
                      예산YoY: budgetYoy,
                      발주YoY: r.yoy,
                      매출YoY: r.revenueYoy,
                    };
                  })}
                  barCategoryGap="20%"
                  barGap={0}
                >
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
                  <XAxis dataKey="name" tick={{ fontSize: 12, fill: '#6b7280' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} axisLine={false} tickLine={false} unit="억" width={50} />
                  <Tooltip
                    contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #e5e7eb' }}
                    formatter={(v, name) => [`${v}억`, name === '예산' ? '올해예산' : name]}
                    itemSorter={() => 0}
                    filterNull={true}
                  />
                  {/* Order: 전년발주 → 올해예산 → AI발주 */}
                  <Bar dataKey="전년발주" fill="#c7d2fe" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="예산" fill="#818cf8" radius={[3, 3, 0, 0]}>
                    <LabelList dataKey="예산YoY" position="top" style={{ fontSize: 10, fill: '#6b7280' }}
                      formatter={v => v ? `${v > 0 ? '+' : ''}${v.toFixed(0)}%` : ''} />
                  </Bar>
                  <Bar dataKey="AI발주" fill="#4f46e5" radius={[3, 3, 0, 0]}>
                    <LabelList dataKey="발주YoY" position="top" style={{ fontSize: 10, fill: '#4f46e5' }}
                      formatter={v => v ? `${v > 0 ? '+' : ''}${v.toFixed(0)}%` : ''} />
                  </Bar>
                  {confirmedAmtByClass2 && (
                    <Bar dataKey="확정발주" fill="#1e293b" radius={[3, 3, 0, 0]}>
                      <LabelList dataKey="확정YoY" position="top" style={{ fontSize: 10, fill: '#1e293b' }}
                        formatter={v => v ? `${v > 0 ? '+' : ''}${v.toFixed(0)}%` : ''} />
                    </Bar>
                  )}
                  {/* Sales: 전년판매 → 판매목표 */}
                  <Bar dataKey="_gap1" fill="transparent" barSize={8} />
                  <Bar dataKey="전년판매" fill="#a7f3d0" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="판매목표" fill="#10b981" radius={[3, 3, 0, 0]}>
                    <LabelList dataKey="매출YoY" position="top" style={{ fontSize: 10, fill: '#059669' }}
                      formatter={v => v ? `${v > 0 ? '+' : ''}${v.toFixed(0)}%` : ''} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <div className="flex items-center justify-center gap-6 mt-2 text-[11px] text-gray-500">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Order</span>
                  <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-indigo-200 inline-block" />전년발주</span>
                  <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-indigo-400 inline-block" />올해예산</span>
                  <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-indigo-700 inline-block" />AI발주</span>
                  {confirmedAmtByClass2 && (
                    <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-slate-800 inline-block" />확정발주</span>
                  )}
                </div>
                <span className="text-gray-200">|</span>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Sales</span>
                  <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-emerald-200 inline-block" />전년판매</span>
                  <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-emerald-500 inline-block" />판매목표</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Gap 알람 배너 + 진단 패널 */}
      {gapAlert && (
        <div className="space-y-0">
          {/* 알람 배너 */}
          <div className={`flex items-center justify-between rounded-xl px-5 py-3 border ${
            gapAlert.isUnder
              ? 'bg-amber-50 border-amber-200'
              : 'bg-red-50 border-red-200'
          } ${showGapPanel ? 'rounded-b-none border-b-0' : ''}`}>
            <div className="flex items-center gap-3">
              <AlertTriangle className={`w-5 h-5 shrink-0 ${gapAlert.isUnder ? 'text-amber-500' : 'text-red-500'}`} />
              <p className={`text-sm ${gapAlert.isUnder ? 'text-amber-700' : 'text-red-700'}`}>
                전체 AI발주가 예산 대비 <span className="font-bold">{Math.abs(gapAlert.totalGap).toFixed(0)}% {gapAlert.isUnder ? '미달' : '초과'}</span>
                {' '}({gapAlert.cats.map(c => `${c.class2} ${Math.abs(c.gap_pct).toFixed(0)}%`).join(', ')})
              </p>
            </div>
            <button
              onClick={() => {
                if (!gapReview && !gapLoading) handleGapReview();
                else setShowGapPanel(!showGapPanel);
              }}
              disabled={gapLoading}
              className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold text-violet-700 bg-violet-50 border border-violet-200 rounded-lg hover:bg-violet-100 disabled:opacity-50 transition-colors"
            >
              {gapLoading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Sparkles className="w-3.5 h-3.5" />
              )}
              {gapLoading ? '분석 중...' : showGapPanel ? 'AI 예산검토 접기' : 'AI 예산검토'}
              {!gapLoading && (showGapPanel ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />)}
            </button>
          </div>

          {/* 진단 패널 */}
          {showGapPanel && gapReview && (
            <div className="bg-white border border-gray-200 border-t-0 rounded-b-xl shadow-sm p-6 space-y-5">
              {/* AI Analysis + AI Suggest 가로 배치 */}
              <div className="grid grid-cols-2 gap-4">
                <div className="bg-slate-50 rounded-lg p-4">
                  <span className={`text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full ${
                    gapReview.source === 'llm'
                      ? 'bg-violet-100 text-violet-700'
                      : 'bg-gray-100 text-gray-500'
                  }`}>
                    AI Analysis
                  </span>
                  <p className="text-sm text-gray-700 leading-relaxed mt-3">{gapReview.review.style_count_comment}</p>
                </div>
                <div className="bg-violet-50 rounded-lg p-4">
                  <span className="text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full bg-violet-100 text-violet-700">
                    AI Suggest
                  </span>
                  <p className="text-sm text-violet-800 leading-relaxed mt-3">{gapReview.review.overall_recommendation}</p>
                </div>
              </div>

              {/* 카테고리별 방향 제안 */}
              <div className="grid grid-cols-1 gap-4">
                {(gapReview.review.category_comments || []).map(cat => (
                  <div key={cat.category} className="border border-gray-100 rounded-lg overflow-hidden">
                    <div className="bg-gray-50 px-4 py-3 flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <span className="text-sm font-bold text-gray-800">{cat.category}</span>
                        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                          cat.gap_summary.includes('미달')
                            ? 'bg-amber-100 text-amber-700'
                            : 'bg-red-100 text-red-700'
                        }`}>
                          {cat.gap_summary}
                        </span>
                      </div>
                      <p className="text-xs text-gray-500 max-w-md text-right">{cat.direction}</p>
                    </div>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-gray-100 text-xs text-gray-500">
                          <th className="px-4 py-2 text-left font-semibold">아이템</th>
                          <th className="px-3 py-2 text-center font-semibold">BCG</th>
                          <th className="px-3 py-2 text-center font-semibold">등급</th>
                          <th className="px-3 py-2 text-center font-semibold">방향</th>
                          <th className="px-4 py-2 text-left font-semibold">제안 근거</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(cat.items || []).map(item => (
                          <tr key={item.item} className="border-b border-gray-50 hover:bg-gray-50/50">
                            <td className="px-4 py-2.5 font-medium text-gray-800">{fmtItem(item.item)}</td>
                            <td className="px-3 py-2.5 text-center">
                              <span className={`text-[11px] font-medium px-1.5 py-0.5 rounded ${
                                item.bcg === 'Star' ? 'bg-yellow-100 text-yellow-700'
                                : item.bcg === 'Cash Cow' ? 'bg-blue-100 text-blue-700'
                                : item.bcg === 'Problem Child' ? 'bg-orange-100 text-orange-700'
                                : 'bg-gray-100 text-gray-600'
                              }`}>
                                {item.bcg}
                              </span>
                            </td>
                            <td className="px-3 py-2.5 text-center">
                              <span className={`text-xs font-bold ${
                                item.grade === 'S' ? 'text-violet-600'
                                : item.grade === 'A' ? 'text-blue-600'
                                : item.grade === 'B' ? 'text-green-600'
                                : item.grade === 'C' ? 'text-amber-600'
                                : 'text-red-500'
                              }`}>
                                {item.grade}
                              </span>
                            </td>
                            <td className="px-3 py-2.5 text-center">
                              <span className={`text-xs font-semibold px-2 py-1 rounded-full ${
                                item.suggestion === '확대 검토' ? 'bg-green-100 text-green-700'
                                : item.suggestion === '축소 검토' ? 'bg-red-100 text-red-700'
                                : 'bg-gray-100 text-gray-600'
                              }`}>
                                {item.suggestion}
                              </span>
                            </td>
                            <td className="px-4 py-2.5 text-xs text-gray-500">{item.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>

            </div>
          )}
        </div>
      )}

      {/* 카테고리별 예산 KPI 요약 */}
      {budgetSummary ? (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-gray-700">카테고리별 예산 KPI 요약</h3>
            {(
              <div className="flex items-center gap-2">
                {rescaleMsg && (
                  <span className={`text-xs ${rescaleMsg.ok ? 'text-green-600' : 'text-red-500'}`}>
                    {rescaleMsg.text}
                  </span>
                )}
                <button
                  onClick={handleRescale}
                  disabled={rescaling}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-violet-700 bg-violet-50 border border-violet-200 rounded-lg hover:bg-violet-100 disabled:opacity-50 transition-colors"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${rescaling ? 'animate-spin' : ''}`} />
                  예산 재적용
                </button>
              </div>
            )}
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600">카테고리</th>
                <th className="px-3 py-3 text-right text-xs font-semibold text-gray-600">전년STY</th>
                <th className="px-3 py-3 text-right text-xs font-semibold text-gray-600">올해STY</th>
                <th className="px-3 py-3 text-right text-xs font-semibold text-gray-600">증감</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">예산(억)</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">AI발주(억)</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">전년발주(억)</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">YoY</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">목표판매율</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">판매목표(억)</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">전년판매(억)</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">매출YoY</th>
              </tr>
            </thead>
            <tbody>
              {budgetSummary.rows.map(row => {
                const isExpanded = expandedCats[row.class2];
                return (
                  <React.Fragment key={row.class2}>
                    <tr
                      className="border-b border-gray-100 hover:bg-gray-50 transition-colors cursor-pointer"
                      onClick={() => setExpandedCats(prev => ({ ...prev, [row.class2]: !prev[row.class2] }))}
                    >
                      <td className="px-4 py-3 font-bold text-gray-800">
                        <span className="inline-block w-4 text-gray-400 text-xs">{isExpanded ? '▼' : '▶'}</span>
                        {row.class2}
                      </td>
                      <td className="px-3 py-3 text-right tabular-nums text-gray-500">{row.prevSty}</td>
                      <td className="px-3 py-3 text-right tabular-nums text-gray-700 font-medium">{row.curSty}</td>
                      <td className={`px-3 py-3 text-right tabular-nums text-xs font-medium ${row.curSty - row.prevSty >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                        {row.curSty - row.prevSty > 0 ? '+' : ''}{row.curSty - row.prevSty}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-gray-700">{formatEok(row.budgetAmt)}</td>
                      <td className="px-4 py-3 text-right tabular-nums font-medium text-gray-900">{formatEok(row.aiOrderAmt)}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-gray-500">{formatEok(row.prevOrdAmt)}</td>
                      <td className={`px-4 py-3 text-right tabular-nums text-xs font-medium ${row.yoy >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                        {row.yoy > 0 ? '+' : ''}{row.yoy.toFixed(1)}%
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-gray-700">{row.targetSTR > 0 ? `${row.targetSTR}%` : '-'}</td>
                      <td className="px-4 py-3 text-right tabular-nums font-medium text-emerald-700">{formatEok(row.targetRevenue)}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-gray-500">{formatEok(row.prevSaleAmt)}</td>
                      <td className={`px-4 py-3 text-right tabular-nums text-xs font-medium ${row.revenueYoy >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                        {row.targetRevenue > 0 ? `${row.revenueYoy > 0 ? '+' : ''}${row.revenueYoy.toFixed(1)}%` : '-'}
                      </td>
                    </tr>
                    {isExpanded && row.items.map(it => (
                      <tr key={`${row.class2}-${it.item}`} className="border-b border-gray-50 bg-gray-50/50">
                        <td className="px-4 py-2 pl-10 text-xs text-gray-600">{fmtItem(it.item)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-xs text-gray-400">{it.prevSty || '-'}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-xs text-gray-600">{it.curSty || '-'}</td>
                        <td className={`px-3 py-2 text-right tabular-nums text-[11px] ${it.curSty - it.prevSty >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                          {it.curSty - it.prevSty > 0 ? '+' : ''}{it.curSty - it.prevSty}
                        </td>
                        <td className="px-4 py-2"></td>
                        <td className="px-4 py-2 text-right tabular-nums text-xs text-gray-700">{it.aiAmt > 0 ? formatEok(it.aiAmt) : '-'}</td>
                        <td className="px-4 py-2 text-right tabular-nums text-xs text-gray-400">{it.prevAmt > 0 ? formatEok(it.prevAmt) : '-'}</td>
                        <td className={`px-4 py-2 text-right tabular-nums text-[11px] ${it.amtYoy >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                          {it.prevAmt > 0 ? `${it.amtYoy > 0 ? '+' : ''}${it.amtYoy.toFixed(0)}%` : it.aiAmt > 0 ? 'NEW' : '-'}
                        </td>
                        <td className="px-4 py-2"></td>
                        <td className="px-4 py-2"></td>
                        <td className="px-4 py-2"></td>
                        <td className="px-4 py-2"></td>
                      </tr>
                    ))}
                  </React.Fragment>
                );
              })}
            </tbody>
            <tfoot className="bg-gray-100 border-t border-gray-200">
              <tr>
                <td className="px-4 py-3 font-bold text-gray-900">합계</td>
                <td className="px-3 py-3 text-right tabular-nums font-bold text-gray-500">{budgetSummary.totalPrevSty}</td>
                <td className="px-3 py-3 text-right tabular-nums font-bold text-gray-800">{budgetSummary.totalCurSty}</td>
                <td className={`px-3 py-3 text-right tabular-nums text-xs font-bold ${budgetSummary.totalCurSty - budgetSummary.totalPrevSty >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                  {budgetSummary.totalCurSty - budgetSummary.totalPrevSty > 0 ? '+' : ''}{budgetSummary.totalCurSty - budgetSummary.totalPrevSty}
                </td>
                <td className="px-4 py-3 text-right tabular-nums font-bold text-gray-800">{formatEok(budgetSummary.totalBudgetAmt)}</td>
                <td className="px-4 py-3 text-right tabular-nums font-bold text-gray-900">{formatEok(budgetSummary.totalAiOrderAmt)}</td>
                <td className="px-4 py-3 text-right tabular-nums font-bold text-gray-600">{formatEok(budgetSummary.totalPrevOrdAmt)}</td>
                <td className={`px-4 py-3 text-right tabular-nums text-xs font-bold ${budgetSummary.totalYoy >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                  {budgetSummary.totalYoy > 0 ? '+' : ''}{budgetSummary.totalYoy.toFixed(1)}%
                </td>
                <td className="px-4 py-3 text-right text-gray-400">-</td>
                <td className="px-4 py-3 text-right tabular-nums font-bold text-emerald-700">{formatEok(budgetSummary.totalTargetRevenue)}</td>
                <td className="px-4 py-3 text-right tabular-nums font-bold text-gray-600">{formatEok(budgetSummary.totalPrevSaleAmt)}</td>
                <td className={`px-4 py-3 text-right tabular-nums text-xs font-bold ${budgetSummary.totalRevenueYoy >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                  {budgetSummary.totalTargetRevenue > 0 ? `${budgetSummary.totalRevenueYoy > 0 ? '+' : ''}${budgetSummary.totalRevenueYoy.toFixed(1)}%` : '-'}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      ) : null /* Lite: 예산 천장 안내 + 재적용 버튼 제거 (예산 미포함) */}

      {/* 스타일별 발주추천 테이블 */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden relative">
        <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-gray-700">
              스타일별 발주추천 (컬러 배분)
            </h3>
            <button
              onClick={() => setShowColorGuide(!showColorGuide)}
              className="p-1 rounded-full hover:bg-blue-50 text-blue-400 transition-colors"
              title="컬러레인지 분류 기준"
            >
              <HelpCircle size={16} />
            </button>
            <span className="w-px h-5 bg-gray-200" />
            <Filter className="w-4 h-4 text-gray-400" />
            <select
              value={categoryFilter}
              onChange={e => setCategoryFilter(e.target.value)}
              className="text-sm border border-gray-200 rounded-lg px-3 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="all">전체 카테고리</option>
              {categories.map(c => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            {confirmMsg && (
              <span className={`text-xs ${confirmMsg.ok ? 'text-green-600' : 'text-red-500'}`}>
                {confirmMsg.text}
              </span>
            )}
            <button
              onClick={handleConfirmOrders}
              disabled={confirming}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-slate-800 rounded-lg hover:bg-slate-700 disabled:opacity-50 transition-colors"
            >
              <CheckCircle className={`w-3.5 h-3.5 ${confirming ? 'animate-spin' : ''}`} />
              수량 확정
            </button>
            <button
              onClick={handleExcelExport}
              className="p-1.5 text-gray-500 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
              title="Excel 다운로드"
            >
              <Download className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* 컬러레인지 분류 가이드 팝오버 */}
        {showColorGuide && (
          <div className="absolute top-14 left-4 z-20 w-[480px] bg-blue-50 border border-blue-200 rounded-xl shadow-lg p-4">
            <div className="flex items-start justify-between mb-3">
              <span className="font-bold text-blue-800 text-sm">컬러레인지 매출비중 (카테고리별)</span>
              <button onClick={() => setShowColorGuide(false)} className="text-blue-400 hover:text-blue-600">
                <X size={16} />
              </button>
            </div>
            {colorShareTable ? (
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-blue-200">
                    <th className="py-1.5 text-left text-blue-700 font-semibold">그룹</th>
                    {colorShareTable.cats.map(cat => (
                      <th key={cat} className="py-1.5 text-right text-blue-700 font-semibold">{cat}</th>
                    ))}
                    <th className="py-1.5 text-right text-blue-700 font-semibold">전체</th>
                  </tr>
                </thead>
                <tbody className="text-blue-900">
                  {colorShareTable.rows.map(r => (
                    <tr key={r.color} className="border-b border-blue-100">
                      <td className="py-1.5">{r.name}</td>
                      {colorShareTable.cats.map(cat => (
                        <td key={cat} className="py-1.5 text-right tabular-nums">{r[cat] > 0 ? `${r[cat]}%` : '-'}</td>
                      ))}
                      <td className="py-1.5 text-right tabular-nums font-medium">{r._all > 0 ? `${r._all}%` : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="text-xs text-blue-600 py-4 text-center">color_mapping.json 로드 중...</p>
            )}
            <p className="text-[11px] text-blue-600 mt-3 pt-2 border-t border-blue-200">
              전시즌 실판매 데이터 (참고용). 컬러배분은 ref스타일 컬러비중과 얼라인되어 산정됩니다.
            </p>
          </div>
        )}
        <div className="max-h-[calc(100vh-300px)] overflow-auto">
          <table className="min-w-[1400px] w-full text-sm table-fixed">
            <thead className="bg-gray-50 border-b border-gray-200 sticky top-0 z-10">
              <tr>
                <th className="w-[3%] px-3 py-3 text-center text-xs font-semibold text-gray-500 whitespace-nowrap">#</th>
                <th className="w-[6%] px-3 py-3 text-left text-xs font-semibold text-gray-500 whitespace-nowrap">복종</th>
                <th className="w-[9%] px-3 py-3 text-left text-xs font-semibold text-gray-400 whitespace-nowrap">Ref_품번</th>
                <th className="w-[5%] px-3 py-3 text-right text-xs font-semibold text-gray-400 whitespace-nowrap">발주</th>
                <th className="w-[8%] px-3 py-3 text-right text-xs font-semibold text-gray-400 whitespace-nowrap">
                  <div>Ref_ST30%</div>
                  <div className="text-[9px] font-normal text-gray-300">누계판매(비중%)</div>
                </th>
                <th className="w-[8%] px-3 py-3 text-right text-xs font-semibold text-gray-400 whitespace-nowrap">
                  <div>Ref_마감</div>
                  <div className="text-[9px] font-normal text-gray-300">누계판매(비중%)</div>
                </th>
                <th className="w-[4%] px-3 py-3 text-right text-xs font-semibold text-gray-400 whitespace-nowrap">
                  <div>ST%</div>
                  <div className="text-[9px] font-normal text-gray-300">판매율%</div>
                </th>
                <th className="w-[11%] px-3 py-3 text-left text-xs font-semibold text-gray-900 bg-blue-50 whitespace-nowrap">기획_품번</th>
                <th className="w-[5%] px-3 py-3 text-right text-xs font-semibold text-gray-900 bg-blue-50 whitespace-nowrap">컬러비중</th>
                <th className="w-[8%] px-3 py-3 text-right text-xs font-semibold text-gray-900 bg-blue-50 whitespace-nowrap">AI추천수량</th>
                <th className="w-[9%] px-3 py-3 text-right text-xs font-semibold text-green-700 bg-green-50 whitespace-nowrap">확정수량</th>
                <th className="w-[11%] px-3 py-3 text-left text-xs font-semibold text-gray-500 whitespace-nowrap">비고</th>
              </tr>
            </thead>
            <tbody className="[&_td]:overflow-hidden [&_td]:text-ellipsis">
              {filtered.map((rec, idx) => {
                const allColors = rec.colors || [];
                // GO 컬러: 비중 순 (기획 제안)
                const goColors = allColors.filter(c => c.color_cd && c.ratio > 0).sort((a, b) => b.ratio - a.ratio);
                // Ref 컬러: 발주 큰 순 (독립 정렬)
                const refColors = allColors.filter(c => c.ref_color_cd && (c.ref_총발주 || 0) > 0).sort((a, b) => (b.ref_총발주 || 0) - (a.ref_총발주 || 0));
                const refSaleTotal = refColors.reduce((sum, c) => sum + (c.ref_총판매 || 0), 0);
                const maxRows = Math.max(goColors.length, refColors.length);
                const hasColors = maxRows > 0;
                const totalRows = hasColors ? 1 + maxRows : 1;
                const isScaled = rec.budget_scaled;
                const isManual = rec.manual_input;

                return (
                  <React.Fragment key={rec.new_part_cd}>
                    {/* 스타일 소계 행 */}
                    <tr className={`border-b ${hasColors ? 'border-gray-100' : 'border-gray-200'} bg-gray-50/60`}>
                      <td rowSpan={totalRows} className="w-[2%] px-1 py-2.5 text-center text-xs text-gray-400 align-top border-r border-gray-100 whitespace-nowrap">
                        {idx + 1}
                      </td>
                      <td rowSpan={totalRows} className="px-[14px] py-2.5 text-xs text-gray-500 align-top border-r border-gray-100">
                        <div className="whitespace-nowrap">{rec.class2 || rec.new_class2}</div>
                        {rec.class2 && rec.class2 !== rec.new_class2 && (
                          <div className="text-[10px] text-gray-400 whitespace-nowrap">{rec.new_class2}</div>
                        )}
                      </td>
                      <td className="px-[14px] py-2.5 text-xs text-gray-400 border-r border-gray-100 whitespace-nowrap">
                        {rec.ref_part_cd ? (
                          <>
                            <span
                              className={`font-mono font-semibold ${(rec.ref_prdt_img || rec.ref_po_img) ? 'text-blue-600 cursor-pointer hover:underline' : 'text-gray-600'}`}
                              onClick={() => (rec.ref_prdt_img || rec.ref_po_img) && setImgPopup({ url: rec.ref_prdt_img || rec.ref_po_img, name: rec.ref_prdt_nm || rec.ref_item_nm, code: rec.ref_part_cd })}
                            >{rec.ref_part_cd}</span>
                            {(rec.ref_prdt_nm || rec.ref_item_nm) && <span className="block text-[10px] text-gray-300">{rec.ref_prdt_nm || rec.ref_item_nm}</span>}
                          </>
                        ) : '-'}
                      </td>
                      <td className="px-[6px] py-2.5 text-right text-xs tabular-nums text-gray-500 border-r border-gray-100 whitespace-nowrap">
                        {rec.ref_총발주 ? rec.ref_총발주.toLocaleString() : '-'}
                      </td>
                      <td className="px-[6px] py-2.5 text-right text-xs text-gray-400 border-r border-gray-100 whitespace-nowrap"></td>
                      <td className="px-[6px] py-2.5 text-right text-xs tabular-nums text-gray-500 border-r border-gray-100 whitespace-nowrap">
                        {rec.ref_총판매 ? rec.ref_총판매.toLocaleString() : '-'}
                      </td>
                      <td className="px-[6px] py-2.5 text-right text-xs tabular-nums text-gray-500 border-r border-gray-100 whitespace-nowrap">
                        {rec.ref_판매율 ? `${rec.ref_판매율.toFixed(1)}%` : '-'}
                      </td>
                      <td className="px-[14px] py-2.5 text-xs font-mono text-gray-800 bg-blue-50/60 border-r border-blue-100 whitespace-nowrap">
                        <span
                          className={`font-semibold ${rec.new_po_img ? 'text-blue-700 cursor-pointer hover:underline' : ''}`}
                          onClick={() => rec.new_po_img && setImgPopup({ url: rec.new_po_img, name: rec.new_prdt_nm || rec.new_item_nm, code: rec.new_part_cd })}
                        >{rec.new_part_cd}</span>
                        {(rec.new_prdt_nm || rec.new_item_nm) && <span className="block text-[10px] text-gray-400 font-sans">{rec.new_prdt_nm || rec.new_item_nm}</span>}
                        {!(rec.new_prdt_nm || rec.new_item_nm) && hasColors && <span className="block text-[10px] text-gray-400 font-sans">합계</span>}
                      </td>
                      <td className="px-[6px] py-2.5 text-right text-xs font-semibold text-gray-600 bg-blue-50/60 whitespace-nowrap">
                        {hasColors ? '100%' : '-'}
                      </td>
                      <td className="px-[14px] py-2.5 text-right text-xs font-bold text-gray-900 bg-blue-50/60 whitespace-nowrap">
                        {(rec.추천발주량 || 0).toLocaleString()}
                      </td>
                      <td className={`px-[14px] py-2.5 text-right text-xs font-bold text-green-700 whitespace-nowrap ${
                        !hasColors && isChanged(confirmedQtys[`${rec.new_part_cd}_`], rec.추천발주량)
                          ? CHANGED_CELL_CLASS
                          : 'bg-green-50/60'
                      }`}>
                        {hasColors ? (
                          getStyleConfirmedTotal(rec).toLocaleString()
                        ) : (
                          <input
                            type="number"
                            value={confirmedQtys[`${rec.new_part_cd}_`] ?? ''}
                            onChange={e => setConfirmedQtys(prev => ({
                              ...prev,
                              [`${rec.new_part_cd}_`]: Number(e.target.value) || 0,
                            }))}
                            className="w-full px-2 py-1 text-xs text-right border border-green-200 rounded bg-white focus:outline-none focus:ring-1 focus:ring-green-400"
                          />
                        )}
                      </td>
                      <td rowSpan={totalRows} className="px-[6px] py-2.5 text-xs text-gray-400 align-top">
                        {isScaled && (
                          <div className="text-red-500">원본 {rec.original_recommendation?.toLocaleString()}</div>
                        )}
                        {isManual && rec.추천발주량 === 0 && (
                          <div className="text-gray-300">매칭 불가</div>
                        )}
                        {isManual && rec.추천발주량 > 0 && (
                          <div className="text-amber-500">수동입력</div>
                        )}
                        {rec.moq_warning && (
                          <div className="text-amber-600">MOQ 확인 필요</div>
                        )}
                      </td>
                    </tr>
                    {/* 컬러별 행 (ref: 발주순, go: 비중순, 독립 나열) */}
                    {Array.from({ length: maxRows }, (_, ci) => {
                      const ref = refColors[ci];
                      const go = goColors[ci];
                      const isLast = ci === maxRows - 1;
                      return (
                        <tr
                          key={`${rec.new_part_cd}-row-${ci}`}
                          className={isLast ? 'border-b border-gray-200' : 'border-b border-gray-50'}
                        >
                          <td className="px-[6px] py-1.5 text-xs text-gray-400 font-mono border-r border-gray-100 whitespace-nowrap">
                            {ref?.ref_color_cd || ''}
                          </td>
                          <td className="px-[6px] py-1.5 text-right text-xs tabular-nums text-gray-400 border-r border-gray-100 whitespace-nowrap">
                            {ref?.ref_총발주 ? ref.ref_총발주.toLocaleString() : ''}
                          </td>
                          <td className="px-[6px] py-1.5 text-right text-xs tabular-nums text-gray-400 border-r border-gray-100 whitespace-nowrap">
                            {ref?.ref_sale_at30 ? (
                              <span>{ref.ref_sale_at30.toLocaleString()}({ref.ref_ratio_at30}%)</span>
                            ) : ''}
                          </td>
                          <td className="px-[6px] py-1.5 text-right text-xs tabular-nums text-gray-400 border-r border-gray-100 whitespace-nowrap">
                            {ref?.ref_총판매 ? (
                              <span>{ref.ref_총판매.toLocaleString()}({refSaleTotal > 0 ? Math.round(ref.ref_총판매 / refSaleTotal * 100) : 0}%)</span>
                            ) : ''}
                          </td>
                          <td className="px-[6px] py-1.5 text-right text-xs tabular-nums text-gray-400 border-r border-gray-100 whitespace-nowrap">
                            {ref?.ref_판매율 ? `${ref.ref_판매율.toFixed(1)}%` : ''}
                          </td>
                          <td className="px-[14px] py-1.5 text-left text-xs text-gray-500 bg-blue-50/40 border-r border-blue-100 whitespace-nowrap">
                            {go && (
                              <>
                                <span className="font-mono">{go.color_cd}</span>
                                {go.role === 'basic' && (
                                  <span className="inline-block px-1.5 py-0.5 text-[10px] font-medium bg-blue-100 text-blue-600 rounded ml-2">Basic</span>
                                )}
                                {go.role === 'sub' && (
                                  <span className="inline-block px-1.5 py-0.5 text-[10px] font-medium bg-emerald-50 text-emerald-600 rounded ml-2">Sub</span>
                                )}
                                {go.role === 'accent' && (
                                  <span className="inline-block px-1.5 py-0.5 text-[10px] font-medium bg-amber-50 text-amber-600 rounded ml-2">Accent</span>
                                )}
                              </>
                            )}
                          </td>
                          <td className="px-[14px] py-1.5 text-right text-xs text-gray-500 bg-blue-50/40 whitespace-nowrap">
                            {go ? `${go.ratio}%` : ''}
                          </td>
                          <td className="px-[14px] py-1.5 text-right text-xs text-gray-700 bg-blue-50/40 whitespace-nowrap">
                            {go ? go.qty.toLocaleString() : ''}
                          </td>
                          <td className={`px-1 py-1 text-right whitespace-nowrap ${
                            go && go.color_cd && isChanged(confirmedQtys[`${rec.new_part_cd}_${go.color_cd}`], go.qty)
                              ? CHANGED_CELL_CLASS
                              : 'bg-green-50/40'
                          }`}>
                            {go && go.color_cd ? (
                              <input
                                type="number"
                                value={confirmedQtys[`${rec.new_part_cd}_${go.color_cd}`] ?? ''}
                                onChange={e => setConfirmedQtys(prev => ({
                                  ...prev,
                                  [`${rec.new_part_cd}_${go.color_cd}`]: Number(e.target.value) || 0,
                                }))}
                                className="w-full px-2 py-1 text-xs text-right border border-green-200 rounded bg-white focus:outline-none focus:ring-1 focus:ring-green-400"
                              />
                            ) : ''}
                          </td>
                        </tr>
                      );
                    })}
                  </React.Fragment>
                );
              })}
            </tbody>
            {filtered.length > 0 && (() => {
              const subtotalAi = filtered.reduce((sum, r) => sum + (r.추천발주량 || 0), 0);
              const subtotalConfirmed = filtered.reduce((sum, r) => sum + getStyleConfirmedTotal(r), 0);
              const label = categoryFilter === 'all' ? 'Total' : `${categoryFilter} 소계`;
              return (
                <tfoot className="sticky bottom-0 z-10">
                  <tr className="border-t-2 border-gray-300 bg-gray-100 shadow-[0_-2px_6px_rgba(0,0,0,0.08)]">
                    <td colSpan={7} className="px-[14px] py-3 text-xs font-bold text-gray-700 text-right">
                      {label} ({filtered.length}개 스타일)
                    </td>
                    <td colSpan={2} className="px-[14px] py-3 bg-blue-50/80"></td>
                    <td className="px-[14px] py-3 text-right text-sm font-bold text-gray-900 bg-blue-50/80 whitespace-nowrap">
                      {subtotalAi.toLocaleString()}
                    </td>
                    <td className="px-[14px] py-3 text-right text-sm font-bold text-green-700 bg-green-50/80 whitespace-nowrap">
                      {subtotalConfirmed.toLocaleString()}
                    </td>
                    <td className="px-[14px] py-3 bg-gray-100"></td>
                  </tr>
                </tfoot>
              );
            })()}
          </table>
        </div>
      </div>
    </div>
  );
}
