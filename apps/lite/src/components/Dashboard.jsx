import React, { useState, useEffect, useMemo } from 'react';
import { useAuth } from '../contexts/AuthContext.jsx';
import { useBrandSeason } from '../contexts/BrandSeasonContext.jsx';
import { createApiClient } from '../service/apiClient.js';
import {
  ComposedChart,
  Line,
  Bar,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { TrendingUp, AlertTriangle, Package, ShoppingCart, ArrowRight, Loader2, Percent, TrendingDown, Search, X } from 'lucide-react';

// --- 1. 툴팁 컴포넌트 (기회비용 표시 로직 포함) ---
const CustomTooltip = ({ active, payload, label, isSuccess, isLowDiag }) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload;
    return (
      <div className="bg-white p-4 border border-gray-200 shadow-lg rounded-lg text-sm z-50">
        <p className="font-bold mb-2 text-gray-700">{label} 주차</p>

        {/* 잠재 판매량 (기회비용 발생 시 표시) — risk/normal 진단은 잠재수요 미표시 */}
        {!isLowDiag && data.potential_sale > data.sale && (
          <p className="text-red-500 flex items-center font-bold mb-1">
            <TrendingUp size={14} className="mr-1" /> 잠재수요: {Math.round(data.potential_sale).toLocaleString()}
            <span className="text-xs ml-1 font-normal">(Loss: -{Math.round(data.potential_sale - data.sale).toLocaleString()})</span>
          </p>
        )}

        <p className="text-blue-600 flex items-center">
          <ShoppingCart size={14} className="mr-1" /> 실판매: {data.sale.toLocaleString()}
        </p>
        <p className="text-gray-500 flex items-center">
          <Package size={14} className="mr-1" /> 재고량: {data.stock.toLocaleString()}
        </p>
        <p className="text-gray-600 flex items-center">
          <Percent size={14} className="mr-1" /> 판매율: {data.sellThrough ? `${data.sellThrough}%` : '-'}
        </p>
        {data.in > 0 && (
          <div className="mt-2 p-1 bg-green-100 text-green-800 rounded font-bold text-center">
            +{data.in.toLocaleString()} 입고
          </div>
        )}
        {data.label && !data.label.includes('리오더') && (
          <div className={`mt-2 p-1 rounded font-bold text-center ${data.label.includes('품절') || data.label.includes('재고부족')
            ? 'bg-red-100 text-red-800'
            : 'bg-gray-100 text-gray-800'
            }`}>
            {data.label.includes('품절') ? '⚠️ ' : ''}
            {data.label}
          </div>
        )}
      </div>
    );
  }
  return null;
};

// --- 2. 범례 컴포넌트 ---
const CustomLegend = ({ payload }) => {
  return (
    <div className="flex justify-end gap-3 mb-2 flex-wrap">
      {payload.map((entry, index) => {
        if (entry.dataKey === 'predicted_sc') {
          return (
            <div key={index} className="flex items-center gap-1">
              <div style={{ width: '20px', height: '0px', borderTop: '2px dashed #dc2626', marginTop: '2px' }}></div>
              <span className="text-xs text-red-600">{entry.value}</span>
            </div>
          );
        } else if (entry.dataKey === 'potential_sale') {
          return (
            <div key={index} className="flex items-center gap-1">
              <div style={{ width: '20px', height: '0px', borderTop: '2.5px solid #dc2626', marginTop: '2px' }}></div>
              <span className="text-xs text-red-600 font-bold">{entry.value}</span>
            </div>
          );
        } else if (entry.dataKey === 'in' || entry.value === '추가입고') {
          return (
            <div key={index} className="flex items-center gap-1">
              <svg width="10" height="10" className="flex-shrink-0">
                <circle cx="5" cy="5" r="4" fill="#2563eb" stroke="white" strokeWidth="1" />
              </svg>
              <span className="text-xs text-gray-700">{entry.value}</span>
            </div>
          );
        } else {
          return (
            <div key={index} className="flex items-center gap-1">
              <div
                style={{
                  width: '12px',
                  height: '12px',
                  backgroundColor: entry.color,
                  borderRadius: entry.type === 'monotone' ? '0' : '2px'
                }}
              />
              <span className="text-xs text-gray-700">{entry.value}</span>
            </div>
          );
        }
      })}
    </div>
  );
};

// --- 3. 메인 차트 섹션 (핵심 로직 수정됨) ---
const ChartSection = ({ title, subTitle, totalData, colorsData, type, diagnosisKey }) => {
  const isSuccess = type === 'success';
  // risk/normal 진단: 잠재수요선·기회비용 미표시 (과잉재고/저판매율 → 품절 기회비용 비논리적)
  const isLowDiag = ['risk', 'normal'].includes(diagnosisKey);
  const titleColor = isSuccess ? 'text-green-700' : 'text-red-700';

  // [수정] 탭 초기값은 항상 'total' (강제 이동 로직 제거)
  const [activeTab, setActiveTab] = useState('total');

  const colorList = colorsData
    ? Object.keys(colorsData)
      .filter(color => (colorsData[color]?.analysis?.총판매 || 0) > 0)
      .sort((a, b) => {
        const aQty = colorsData[a]?.analysis?.총발주 || 0;
        const bQty = colorsData[b]?.analysis?.총발주 || 0;
        return bQty - aQty;
      })
    : [];

  const getCurrentData = () => {
    if (activeTab === 'total' && totalData) {
      return totalData;
    } else if (activeTab !== 'total' && colorsData && colorsData[activeTab]) {
      return colorsData[activeTab];
    }
    return null;
  };

  const currentData = getCurrentData();
  const rawData = currentData?.chartData || [];
  const itemInfo = currentData?.itemInfo || {};
  const analysis = currentData?.analysis || {};

  let cumSale = 0;
  let cumIn = 0;

  // 데이터 가공 및 '잠재 수요' 필드 확보 (Python에서 이미 필터링됨)
  const data = rawData.map(item => {
    const sale = Math.max(0, item.sale || 0);
    const stock = Math.max(0, item.stock || 0);
    const inQty = Math.max(0, item.in || 0);
    cumSale += sale;
    cumIn += inQty;
    const sellThrough = cumIn > 0 ? (cumSale / cumIn * 100) : 0;

    const potential_sale = item.potential_sale !== undefined ? item.potential_sale : (item.predicted_sale || 0);
    const actual_tax = Math.max(0, item.actual_tax || 0);
    const tax_free_sale = Math.max(0, sale - actual_tax);
    const predicted_sc = item.predicted_sc !== undefined ? item.predicted_sc : 0;

    return {
      ...item,
      sale,
      stock,
      in: inQty,
      cumSale,
      cumIn,
      potential_sale,
      actual_tax,
      tax_free_sale,
      predicted_sc,
      sellThrough: Math.round(sellThrough * 10) / 10
    };
  });

  const areaColor = isSuccess ? '#dcfce7' : '#fee2e2';
  const stockStroke = isSuccess ? '#16a34a' : '#dc2626';

  // [수정] 단가 하드코딩 제거 (데이터에서 가져오거나 없으면 0)
  const price = itemInfo.price || totalData?.itemInfo?.price || 0;

  // 총 기회비용 수량 = 실현(과거) + 잠재(미래) 합산 표기
  //  · 과거: 결품으로 이미 놓친 분 = Python loss 필드 (없으면 전체수요−실판매)
  //  · 미래(forecast): 현재고로 못 채우는 미충족분 = 전체수요(빨강) − 마감예측(회색 eofs)
  //  · 마감/FW 시즌은 forecast 포인트가 없어 자동으로 실현분만 (기존값 유지)
  const totalLossQty = Math.round(rawData.reduce((acc, cur) => {
    if (cur.is_forecast) {
      const p = cur.potential_sale || 0;
      const e = cur.eofs_sale || 0;
      return acc + (p > e ? p - e : 0);   // 잠재 기회비용(미래 미충족)
    }
    // 실현 기회비용(과거)
    if (cur.loss !== undefined && cur.loss !== null) {
      return acc + cur.loss;
    }
    const p = cur.potential_sale || 0;
    const s = cur.sale || 0;
    return acc + (p > s ? p - s : 0);
  }, 0));   // 수량은 정수 표시

  const estimatedLossAmount = totalLossQty * price;

  // Shortage 진단이면 기회비용 소량이어도 항상 표시
  const diagText = analysis['AI_진단'] || analysis['진단'] || totalData?.analysis?.['AI_진단'] || '';
  const isShortage = diagText.includes('Shortage');
  const hasForecast = rawData.some(p => p.is_forecast);   // in-season 마감예측 포인트 존재 시
  // risk/normal은 잠재수요선·기회비용 카드 모두 숨김 (마감예측 회색막대·실판매는 유지)
  const showLoss = !isLowDiag && (totalLossQty >= 30 || (isShortage && totalLossQty > 0) || hasForecast);

  return (
    <div className="bg-white p-6 rounded-xl shadow-md border border-gray-100 min-w-0 flex flex-col h-full">
      <div className="flex justify-between items-start mb-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            {isSuccess ? <TrendingUp className="text-green-600" /> : <AlertTriangle className="text-red-600" />}
            <h3 className={`text-lg font-bold ${titleColor}`}>{title}</h3>
          </div>
          <p className="text-gray-500 text-sm">{subTitle}</p>
        </div>
        <div className="text-right text-xs">
          <p className="font-bold text-gray-800">{itemInfo.name || totalData?.itemInfo?.name || ''}</p>
          <p className="text-gray-500">
            {itemInfo.code || totalData?.itemInfo?.code || ''}
            {itemInfo.color && itemInfo.color !== '전체' ? ` / ${itemInfo.color}` : ''}
          </p>
          {/* 가격 정보가 있으면 표시 */}
          {price > 0 && <p className="text-gray-400 mt-1">￦{price.toLocaleString()}</p>}
        </div>
      </div>

      {/* 탭 버튼 영역 */}
      <div className="mb-2 border-b border-gray-200 overflow-x-auto">
        <div className="flex gap-2 min-w-max">
          <button
            onClick={() => setActiveTab('total')}
            className={`px-3 py-1.5 text-xs font-bold transition-colors whitespace-nowrap ${activeTab === 'total'
              ? 'text-blue-600 border-b-2 border-blue-600'
              : 'text-gray-500 hover:text-gray-700'
              }`}
          >
            스타일 전체
          </button>
          {colorList.map(color => {
            const colorTotalLoss = colorsData?.[color]?.chartData?.reduce((sum, p) => sum + (p.loss || 0), 0) || 0;
            const colorDiag = colorsData?.[color]?.analysis?.['AI_진단'] || '';
            const colorIsShortage = colorDiag.includes('Shortage');
            const colorHasLoss = colorTotalLoss >= 30 || (colorIsShortage && colorTotalLoss > 0);
            return (
              <button
                key={color}
                onClick={() => setActiveTab(color)}
                className={`px-3 py-1.5 text-xs font-medium transition-colors whitespace-nowrap ${activeTab === color
                  ? 'text-blue-600 border-b-2 border-blue-600'
                  : 'text-gray-500 hover:text-gray-700'
                  }`}
              >
                {color}{colorHasLoss ? ' 🔻' : ''}
              </button>
            );
          })}
        </div>
      </div>

      <div className="h-64 w-full mb-4">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 10, right: 10, bottom: 0, left: -20 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} interval={2} />
            <YAxis yAxisId="left" orientation="left" stroke="#8884d8" domain={[0, 'auto']} allowDataOverflow={true} tick={{ fontSize: 11 }} width={35} />
            <YAxis yAxisId="right" orientation="right" stroke="#82ca9d" domain={[0, 'auto']} allowDataOverflow={true} tick={{ fontSize: 11 }} width={35} />
            <Tooltip content={<CustomTooltip isSuccess={isSuccess} isLowDiag={isLowDiag} />} />
            <Legend content={<CustomLegend />} verticalAlign="top" height={24} />

            <Area yAxisId="left" type="monotone" dataKey="stock" fill={areaColor} stroke={stockStroke} name="Stock" fillOpacity={0.6} />

            {/* 국내판매 - 진한 파란색 (stacked 하단) */}
            <Bar yAxisId="right" dataKey="actual_tax" stackId="sales" barSize={14} fill="rgba(37,99,235,0.85)" name="순수국내" />

            {/* 면세판매 - 연한 파란색 (stacked 상단, sale − actual_tax) */}
            <Bar yAxisId="right" dataKey="tax_free_sale" stackId="sales" barSize={14} fill="rgba(186,220,255,0.7)" name="전체판매" radius={[2, 2, 0, 0]} />

            {/* 마감예측(Track2) - 미래주차 연한 회색 막대 (전체판매에서 자연 연결, 표시 전용) */}
            {hasForecast && (
              <Bar yAxisId="right" dataKey="eofs_sale" stackId="sales" barSize={14} fill="rgba(148,163,184,0.5)" name="마감예측" radius={[2, 2, 0, 0]} />
            )}

            {/* S5 국내수요예측 - 붉은색 점선 (Bar 뒤에 선언 → 최상단 표시) */}
            {showLoss && (
              <Line
                yAxisId="right"
                type="monotone"
                dataKey="predicted_sc"
                stroke="#dc2626"
                strokeWidth={1.5}
                strokeDasharray="6 3"
                dot={false}
                name="국내수요"
                activeDot={{ r: 3, strokeWidth: 0 }}
              />
            )}

            {/* S5 전체수요예측 ★ - 붉은색 실선 (굵게, 가장 앞) */}
            {showLoss && (
              <Line
                yAxisId="right"
                type="monotone"
                dataKey="potential_sale"
                stroke="#dc2626"
                strokeWidth={2.5}
                dot={false}
                name="전체수요"
                activeDot={{ r: 4, strokeWidth: 0 }}
              />
            )}

            <Line yAxisId="left" type="step" dataKey="in" stroke="none"
              dot={(props) => {
                const { cx, cy, payload } = props;
                if (payload.in > 0) {
                  return (
                    <g key={`dot-${payload.date}`}>
                      <circle cx={cx} cy={cy} r={4} fill="#2563eb" stroke="white" strokeWidth={1} />
                      <text x={cx} y={cy - 10} textAnchor="middle" fill="#2563eb" fontSize={10} fontWeight="bold">입고</text>
                    </g>
                  );
                }
                return <></>;
              }}
              name="InStock"
            />

          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-auto flex gap-0 rounded-lg overflow-hidden text-xs bg-gray-50 border border-gray-200">
        <div className="p-3 border-r border-gray-200" style={{ width: '30%' }}>
          <div className="flex flex-col gap-1">
            <div className="flex justify-between">
              <span className="text-gray-500">누계발주</span>
              <span className="font-bold">{analysis['총발주']?.toLocaleString() || '-'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">누계입고</span>
              <span className="font-bold">{analysis['총입고']?.toLocaleString() || '-'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">누계판매</span>
              <span className="font-bold">{analysis['총판매']?.toLocaleString() || '-'}</span>
            </div>
            <div className="flex justify-between items-center mt-1 pt-1 border-t border-gray-200">
              <span className="text-gray-600 font-bold">누계 ST%</span>
              <span className={`font-bold ${analysis['최종판매율'] >= 80 ? 'text-green-600' : analysis['최종판매율'] <= 40 ? 'text-red-600' : 'text-gray-800'}`}>
                {analysis['최종판매율']}%
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-gray-600 font-bold">예상 ST%</span>
              <span className="font-bold text-gray-800">
                {analysis['fcst_예측마감판매율'] != null ? `${analysis['fcst_예측마감판매율']}%` : '-'}
              </span>
            </div>
          </div>
        </div>

        <div className="p-3" style={{ width: '70%' }}>
          <h4 className="font-bold mb-1 flex items-center text-gray-800">
            <ArrowRight size={12} className="mr-1" /> AI 패턴 진단
          </h4>

          {showLoss ? (
            <div className="space-y-2">
              <p className="text-red-600 leading-tight">
                시즌 중 물량 공백으로 매출로스가 발생되었습니다.
              </p>
              <div className="bg-white border border-red-200 rounded p-2 flex items-center justify-between shadow-sm">
                <div className="flex items-center text-red-600">
                  <TrendingDown size={14} className="mr-1" />
                  <span className="font-bold">기회비용</span>
                </div>
                <div className="text-right">
                  <div className="font-bold text-red-600">-{totalLossQty.toLocaleString()}장</div>
                  <div className="text-[10px] text-red-400">약 {estimatedLossAmount.toLocaleString()}원</div>
                </div>
              </div>
            </div>
          ) : isSuccess ? (
            <p className="leading-tight text-green-700">
              초도 발주 적중율이 높았거나 시즌 중 적시 리오더 투입으로 판매 모멘텀을 유지한 Best Practice입니다.
            </p>
          ) : (
            <p className="leading-tight text-gray-600">
              판매 추이가 정상적이나 재고 운영 효율화가 필요합니다.
            </p>
          )}
        </div>
      </div>
    </div>
  );
};

// --- 4. 진단명 필터 옵션 정의 ---
const DIAGNOSIS_OPTIONS = {
  success: [
    { key: 'hit', label: '🟢 Hit', description: '판매율 65% 이상, 적기 소진된 우수 케이스입니다.' },
    { key: 'normal', label: '⚪ Normal', description: '정상 판매 (45~65%)' }
  ],
  failure: [
    { key: 'shortage', label: '⚠️ Shortage', description: '판매율 65% 이상이나 시즌 중 재고 부족이 발생한 케이스입니다.' },
    { key: 'risk', label: '🔴 Risk', description: '판매율 45% 미만' }
  ]
};

// 진단별 디폴트 스타일 코드 (해당 코드가 목록에 있으면 우선 선택, 없으면 idx=0)
const DEFAULT_STYLE_CODES = {
  hit: '3ADJB2156',
  shortage: '3AMTB0154',
};

const sortByTotalSaleDesc = (list) =>
  (list || []).slice().sort((a, b) => (b?.total?.analysis?.총판매 || 0) - (a?.total?.analysis?.총판매 || 0));

const resolveDefaultIdx = (sortedList, diagnosisKey) => {
  const targetCode = DEFAULT_STYLE_CODES[diagnosisKey];
  if (!targetCode) return 0;
  const idx = sortedList.findIndex((item) => item?.total?.itemInfo?.code === targetCode);
  return idx >= 0 ? idx : 0;
};

// --- 5. 최상위 앱 컴포넌트 ---
const App = () => {
  const { user } = useAuth();
  const { brand, season } = useBrandSeason();
  const api = useMemo(
    () => createApiClient(user?.email, brand, season, user?.role),
    [user?.email, brand, season],
  );
  const [loading, setLoading] = useState(true);
  const [rawData, setRawData] = useState({ success: {}, failure: {} });

  // 진단명 필터 상태
  const [selectedSuccessDiagnosis, setSelectedSuccessDiagnosis] = useState('hit');
  const [selectedFailureDiagnosis, setSelectedFailureDiagnosis] = useState('shortage');

  // 스타일 선택 상태
  const [selectedSuccessIdx, setSelectedSuccessIdx] = useState(0);
  const [selectedFailureIdx, setSelectedFailureIdx] = useState(0);

  // 스타일코드 검색
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const searchRef = React.useRef(null);
  // 검색에서 선택한 idx를 진단 변경 useEffect가 덮어쓰지 않도록 임시 보관
  const pendingSuccessIdxRef = React.useRef(null);
  const pendingFailureIdxRef = React.useRef(null);

  useEffect(() => {
    // 실제 데이터 파일 로드
    const loadDashboardData = async () => {
      try {
        const data = await api.fetchFile('dashboard_data.json');
        if (!data) {
          throw new Error('데이터 파일을 찾을 수 없습니다.');
        }

        // 4분류 플랫 구조
        const parsed = {
          success: { hit: data.hit || [], normal: data.normal || [] },
          failure: { shortage: data.shortage || [], risk: data.risk || [] }
        };
        console.log('[DEBUG] normal count:', parsed.success.normal.length);
        console.log('[DEBUG] normal[0] code:', parsed.success.normal[0]?.total?.itemInfo?.code);
        setRawData(parsed);
        // 디폴트: 진단별 지정 스타일(DEFAULT_STYLE_CODES) 우선, 없으면 정렬 1위
        setSelectedSuccessIdx(resolveDefaultIdx(sortByTotalSaleDesc(parsed.success.hit), 'hit'));
        setSelectedFailureIdx(resolveDefaultIdx(sortByTotalSaleDesc(parsed.failure.shortage), 'shortage'));
        setLoading(false);
      } catch (error) {
        console.error('데이터 로드 실패:', error);
        setLoading(false);
      }
    };

    loadDashboardData();
  }, [api]);

  // 현재 선택된 진단의 데이터 배열
  // 상품목록 정렬: 마감판매수량(총판매) 내림차순
  const successData = sortByTotalSaleDesc(rawData.success[selectedSuccessDiagnosis]);
  const failureData = sortByTotalSaleDesc(rawData.failure[selectedFailureDiagnosis]);

  // 진단 변경 시 디폴트 스타일 복원 (Hit→3ADJB2156, Shortage→3AMTB0154, 없으면 idx=0)
  // 단, 검색에서 선택한 경우(pendingRef에 값이 있음)는 해당 idx를 우선 적용
  useEffect(() => {
    if (pendingSuccessIdxRef.current != null) {
      setSelectedSuccessIdx(pendingSuccessIdxRef.current);
      pendingSuccessIdxRef.current = null;
    } else {
      setSelectedSuccessIdx(resolveDefaultIdx(successData, selectedSuccessDiagnosis));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSuccessDiagnosis]);

  useEffect(() => {
    if (pendingFailureIdxRef.current != null) {
      setSelectedFailureIdx(pendingFailureIdxRef.current);
      pendingFailureIdxRef.current = null;
    } else {
      setSelectedFailureIdx(resolveDefaultIdx(failureData, selectedFailureDiagnosis));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFailureDiagnosis]);

  // 스타일코드 검색 결과 (모든 진단 카테고리에서 탐색)
  const searchResults = React.useMemo(() => {
    if (!searchQuery || searchQuery.length < 2) return [];
    const q = searchQuery.toUpperCase();
    const results = [];
    const allCategories = [
      ...DIAGNOSIS_OPTIONS.success.map(o => ({ ...o, side: 'success' })),
      ...DIAGNOSIS_OPTIONS.failure.map(o => ({ ...o, side: 'failure' })),
    ];
    allCategories.forEach(({ key, label, side }) => {
      const list = side === 'success' ? rawData.success[key] : rawData.failure[key];
      if (!list) return;
      // 정렬된 데이터 기준 idx 산출 (successData/failureData와 일치시켜야 정확)
      const sorted = sortByTotalSaleDesc(list);
      sorted.forEach((item, idx) => {
        const code = item.total?.itemInfo?.code || '';
        const name = item.total?.itemInfo?.prdt_nm || item.total?.itemInfo?.name || '';
        if (code.toUpperCase().includes(q) || name.toUpperCase().includes(q)) {
          results.push({ code, name, diagnosis: key, diagLabel: label, side, idx });
        }
      });
    });
    return results.slice(0, 10);
  }, [searchQuery, rawData]);

  const handleSearchSelect = (result) => {
    if (result.side === 'success') {
      // 같은 진단이면 useEffect가 트리거되지 않으므로 idx를 직접 설정,
      // 다른 진단이면 ref에 idx를 저장 후 진단 변경 (useEffect가 ref 값 적용)
      if (result.diagnosis === selectedSuccessDiagnosis) {
        setSelectedSuccessIdx(result.idx);
      } else {
        pendingSuccessIdxRef.current = result.idx;
        setSelectedSuccessDiagnosis(result.diagnosis);
      }
    } else {
      if (result.diagnosis === selectedFailureDiagnosis) {
        setSelectedFailureIdx(result.idx);
      } else {
        pendingFailureIdxRef.current = result.idx;
        setSelectedFailureDiagnosis(result.diagnosis);
      }
    }
    setSearchOpen(false);
    setSearchQuery('');
  };

  // 검색창 외부 클릭 시 닫기
  React.useEffect(() => {
    if (!searchOpen) return;
    const handler = (e) => {
      if (searchRef.current && !searchRef.current.contains(e.target)) {
        setSearchOpen(false);
        setSearchQuery('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [searchOpen]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <Loader2 className="animate-spin mx-auto mb-4 text-blue-600" size={48} />
          <p className="text-gray-600">AI 분석 데이터를 불러오는 중...</p>
        </div>
      </div>
    );
  }

  if (successData.length === 0 && failureData.length === 0) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center bg-white p-8 rounded-xl shadow-md">
          <AlertTriangle className="mx-auto mb-4 text-yellow-600" size={48} />
          <h2 className="text-xl font-bold text-gray-900 mb-2">데이터가 없습니다</h2>
          <p className="text-gray-600 mb-4">분석된 데이터가 없습니다.</p>
        </div>
      </div>
    );
  }

  const currentSuccessData = successData[selectedSuccessIdx];
  const currentFailureData = failureData[selectedFailureIdx];

  return (
    <div className="relative">
      {/* 스타일코드 검색 아이콘 */}
      <div className="absolute right-0 -top-10 z-20" ref={searchRef}>
        {!searchOpen ? (
          <button
            onClick={() => { setSearchOpen(true); setTimeout(() => searchRef.current?.querySelector('input')?.focus(), 50); }}
            className="p-1.5 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all"
            title="스타일코드 검색"
          >
            <Search size={16} />
          </button>
        ) : (
          <div className="relative">
            <div className="flex items-center gap-1 bg-white border border-gray-300 rounded-md px-2 py-1 shadow-sm" style={{ width: '280px' }}>
              <Search size={14} className="text-gray-400 flex-shrink-0" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="스타일코드 또는 품명 검색..."
                className="flex-1 text-sm outline-none bg-transparent"
                autoFocus
              />
              <button onClick={() => { setSearchOpen(false); setSearchQuery(''); }} className="text-gray-400 hover:text-gray-600">
                <X size={14} />
              </button>
            </div>
            {searchResults.length > 0 && (
              <div className="absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-50 w-[360px] max-h-[300px] overflow-y-auto">
                {searchResults.map((r, i) => (
                  <button
                    key={i}
                    onClick={() => handleSearchSelect(r)}
                    className="w-full text-left px-3 py-2 hover:bg-blue-50 border-b border-gray-50 last:border-0 flex items-center gap-2"
                  >
                    <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 flex-shrink-0">{r.diagLabel}</span>
                    <span className="text-sm font-mono font-medium text-gray-800">{r.code}</span>
                    <span className="text-xs text-gray-500 truncate">{r.name}</span>
                  </button>
                ))}
              </div>
            )}
            {searchQuery.length >= 2 && searchResults.length === 0 && (
              <div className="absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-50 w-[280px] px-3 py-2 text-sm text-gray-400">
                검색 결과 없음
              </div>
            )}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* 성공 사례 */}
          <div className="h-full flex flex-col gap-3">
            {/* 2개 드롭다운: 진단 필터 + 스타일 선택 */}
            <div className="bg-white p-3 rounded-lg shadow-sm border border-gray-200">
              <div className="flex gap-3">
                {/* 진단 필터 */}
                <div className="flex-1">
                  <label className="block text-xs font-bold text-green-600 mb-2">
                    AI 진단
                  </label>
                  <select
                    value={selectedSuccessDiagnosis}
                    onChange={(e) => setSelectedSuccessDiagnosis(e.target.value)}
                    className="w-full px-3 py-2 border border-green-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-green-500 bg-green-50"
                  >
                    {DIAGNOSIS_OPTIONS.success.map((opt) => (
                      <option key={opt.key} value={opt.key}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
                {/* 스타일 선택 */}
                <div className="flex-[2]">
                  <label className="block text-xs font-bold text-gray-600 mb-2">
                    PRODUCT ({successData.length}sty)
                  </label>
                  <select
                    value={selectedSuccessIdx}
                    onChange={(e) => setSelectedSuccessIdx(Number(e.target.value))}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-green-500"
                    disabled={successData.length === 0}
                  >
                    {successData.length === 0 ? (
                      <option>해당 진단 데이터 없음</option>
                    ) : (
                      successData.map((item, idx) => {
                        const code = item.total.itemInfo.code;
                        const prdtNm = item.total.itemInfo.prdt_nm || item.total.itemInfo.name;
                        const totalLoss = item.colors ? Object.values(item.colors).reduce((sum, c) =>
                          sum + (c.chartData?.reduce((s, p) => s + (p.loss || 0), 0) || 0), 0) : 0;
                        const hasLoss = totalLoss >= 30;
                        return (
                          <option key={idx} value={idx}>
                            {hasLoss ? '🔻 ' : ''}{code} - {prdtNm}
                          </option>
                        );
                      })
                    )}
                  </select>
                </div>
              </div>
            </div>

            {successData.length > 0 ? (
              <ChartSection
                type="success"
                title="Success Case"
                subTitle={DIAGNOSIS_OPTIONS.success.find(o => o.key === selectedSuccessDiagnosis)?.description || ''}
                diagnosisKey={selectedSuccessDiagnosis}
                totalData={currentSuccessData?.total}
                colorsData={currentSuccessData?.colors}
              />
            ) : (
              <div className="bg-white p-6 rounded-xl shadow-md border border-gray-100 flex items-center justify-center h-64">
                <p className="text-gray-400">선택한 진단에 해당하는 데이터가 없습니다.</p>
              </div>
            )}
          </div>

          {/* 실패 사례 */}
          <div className="h-full flex flex-col gap-3">
            {/* 2개 드롭다운: 진단 필터 + 스타일 선택 */}
            <div className="bg-white p-3 rounded-lg shadow-sm border border-gray-200">
              <div className="flex gap-3">
                {/* 진단 필터 */}
                <div className="flex-1">
                  <label className="block text-xs font-bold text-red-600 mb-2">
                    AI 진단
                  </label>
                  <select
                    value={selectedFailureDiagnosis}
                    onChange={(e) => setSelectedFailureDiagnosis(e.target.value)}
                    className="w-full px-3 py-2 border border-red-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-red-500 bg-red-50"
                  >
                    {DIAGNOSIS_OPTIONS.failure.map((opt) => (
                      <option key={opt.key} value={opt.key}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
                {/* 스타일 선택 */}
                <div className="flex-[2]">
                  <label className="block text-xs font-bold text-gray-600 mb-2">
                    PRODUCT ({failureData.length}sty)
                  </label>
                  <select
                    value={selectedFailureIdx}
                    onChange={(e) => setSelectedFailureIdx(Number(e.target.value))}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-red-500"
                    disabled={failureData.length === 0}
                  >
                    {failureData.length === 0 ? (
                      <option>해당 진단 데이터 없음</option>
                    ) : (
                      failureData.map((item, idx) => {
                        const code = item.total.itemInfo.code;
                        const prdtNm = item.total.itemInfo.prdt_nm || item.total.itemInfo.name;
                        const totalLoss = item.colors ? Object.values(item.colors).reduce((sum, c) =>
                          sum + (c.chartData?.reduce((s, p) => s + (p.loss || 0), 0) || 0), 0) : 0;
                        const itemDiag = item.total?.analysis?.['AI_진단'] || '';
                        const hasLoss = totalLoss >= 30 || (itemDiag.includes('Shortage') && totalLoss > 0);
                        return (
                          <option key={idx} value={idx}>
                            {hasLoss ? '🔻 ' : ''}{code} - {prdtNm}
                          </option>
                        );
                      })
                    )}
                  </select>
                </div>
              </div>
            </div>

            {failureData.length > 0 ? (
              <ChartSection
                type="failure"
                title="Failure Case"
                subTitle={DIAGNOSIS_OPTIONS.failure.find(o => o.key === selectedFailureDiagnosis)?.description || ''}
                diagnosisKey={selectedFailureDiagnosis}
                totalData={currentFailureData?.total}
                colorsData={currentFailureData?.colors}
              />
            ) : (
              <div className="bg-white p-6 rounded-xl shadow-md border border-gray-100 flex items-center justify-center h-64">
                <p className="text-gray-400">선택한 진단에 해당하는 데이터가 없습니다.</p>
              </div>
            )}
          </div>
      </div>
    </div>
  );
};

export default App;