'use strict';

// AI 業務擂台：「新增情境」三步驟（商品 → 客戶人設 → 確認大綱）、Role Play 劇本、下載 Word、情境檔的匯出匯入。
// 內容來自「Role Play 劇本與超業範本生成器」：人設卡、對話範本、異議表、必揭露風險、評分指標都照原樣搬進來，
// 劇本和情境不靠 AI，同樣的輸入每次產出一樣的結果；只有第一步「上傳商品檔」會請 AI 讀內容、幫忙填欄位（填完還是給人核對）。
// 上半段是資料和純邏輯（不碰畫面，Node 也能測）；下半段是畫面，由 app.js 呼叫 Arena.init() 接上。

const Arena = (() => {
  // ------------------------------------------------------------------ 資料（照生成器原樣）

  // 商品類型，照這個順序排（FCN 排第一個，新增情境時預設也選它）
  const TYPES = { fcn: 'FCN', bond: '海外債', fund: '共同基金', custom: '自訂其他商品' };
  const RUBRIC = { fcn: 'general', bond: 'bond', fund: 'fund', custom: 'general' }; // 對到 App 產品面的三種評分標準
  const UNITS = ['財經部', '金商部', '業管部'];
  // 商品資料全部的欄位（存在劇本裡的名字）；每種類型只會用到其中幾個
  const FK = ['name', 'issuer', 'code', 'ccy', 'risk', 'under', 'coupon', 'tenor', 'strike', 'rating', 'call', 'ki', 'extra', 'risks'];
  // 每種商品類型要填哪些欄位、畫面上叫什麼：基金不是債券，沒有票息、信用評等、提前贖回這些，要用基金自己的說法
  const FIELDS = {
    fcn: [['name', '商品名稱'], ['issuer', '發行機構'], ['code', '商品代號 ISIN'], ['ccy', '計價幣別'], ['risk', '風險等級'], ['under', '連結標的'],
      ['coupon', '固定配息率'], ['tenor', '天期／到期日'], ['strike', '履約價'], ['call', '提前出場條件（KO）'], ['ki', '下限價（KI）'], ['rating', '發行機構信用評等'],
      ['extra', '額外條件'], ['risks', '必揭露風險清單']],
    bond: [['name', '商品名稱'], ['issuer', '發行機構'], ['code', '商品代號 ISIN'], ['ccy', '計價幣別'], ['risk', '風險等級'], ['coupon', '票息條件'],
      ['tenor', '到期日／年期'], ['rating', '債券信用評等'], ['call', '提前贖回條件'], ['extra', '額外條件'], ['risks', '必揭露風險清單']],
    fund: [['name', '基金名稱'], ['issuer', '基金公司／投信'], ['code', '基金代碼／ISIN'], ['ccy', '計價幣別'], ['risk', '風險等級'], ['coupon', '配息機制與頻率'],
      ['tenor', '基金類型／閉鎖期'], ['call', '贖回限制與費用'], ['extra', '額外條件'], ['risks', '必揭露風險清單']],
    custom: [['name', '商品名稱'], ['issuer', '發行／管理機構'], ['code', '商品代號'], ['ccy', '計價幣別'], ['risk', '風險等級'], ['coupon', '收益條件'],
      ['tenor', '期間'], ['rating', '信用評等'], ['call', '提前終止條件'], ['extra', '額外條件'], ['risks', '必揭露風險清單']],
  };
  const fieldsOf = (type) => FIELDS[type] || FIELDS.bond;
  const PH = {
    fcn: { name: '例：美元計價 6 個月期 FCN', issuer: '發行／保證機構全名', code: 'ISIN 碼', ccy: '例：美元 USD', under: '例：NVDA、TSLA（表現最差的一檔決定結果）', coupon: '例：年化 12%，每月配息', tenor: '例：6 個月', strike: '例：期初價格的 85%', call: '例：每月觀察，全部標的 ≥ 期初價 100% 就提前出場', ki: '例：期初價格的 65%（沒有就留空）', rating: '例：S&P A+', extra: '例：最低申購金額、閉鎖期', risks: '一行一項，例：1. 非保本' },
    bond: { name: '例：某某公司 20 年期美元債', issuer: '發行機構全名', code: 'ISIN 碼', ccy: '例：美元 USD', coupon: '票息率、付息頻率', tenor: '到期日／年期', rating: '例：Fitch A／S&P BBB+', call: '例：第 X 年起發行商有權提前贖回', extra: '例：閉鎖期、最低申購金額', risks: '一行一項，例：1. 信用風險' },
    fund: { name: '基金名稱', issuer: '基金公司／投信', code: '基金代碼／ISIN', ccy: '計價幣別', coupon: '配息機制與頻率', tenor: '閉鎖期／基金型態', rating: '（基金可留空）', call: '贖回限制', extra: '例：成立後閉鎖期 90 天', risks: '一行一項' },
    custom: { name: '商品名稱', issuer: '發行／管理機構', code: '商品代號', ccy: '計價幣別', coupon: '收益條件', tenor: '期間', rating: '信用評等', call: '提前終止條件', extra: '其他條件', risks: '一行一項' },
  };
  const RISK_DEF = {
    fcn: ['非保本商品，最壞情況可能損失全部本金', '連結標的下跌風險：到期若跌破約定價格，可能要用履約價承接表現最差的標的，本金會虧損', '發行機構信用風險', '流動性風險：到期前不易賣出，提前贖回可能折價', '提前出場與再投資風險', '匯率風險', '非存款保險標的，配息固定不代表保本'],
    bond: ['發行機構信用／違約風險', '利率風險：賣出價格可能低於買進成本', '流動性風險：買賣價差大、可能無法即時賣出', '提前贖回與再投資風險', '匯率風險', '非存款保險標的，不保證收益，可能損失本金'],
    fund: ['市場價格波動與本金虧損風險', '配息可能由本金支付，配息率不代表報酬', '匯率風險', '流動性與贖回限制風險', '過去績效不代表未來表現', '非存款保險標的，不保證收益，可能損失本金'],
    custom: ['市場價格波動風險', '信用風險', '流動性風險', '匯率風險', '非存款保險標的，不保證收益，可能損失本金'],
  };
  const PHRASES = ['你講白話一點', '定存至少不會少', '沒賺少賺沒關係，但賠錢出去很沒面子捏', '20年期太長，跌下來誰賠？', '你說流動性好，想賣時價格爛死根本賣不掉', '提前買回我不就等於被佔便宜？', '這跟ETF比差在哪？', '每個月要讓我看到錢'];
  const DIFFS = { 1: '溫和友善（初始信任度 60）', 2: '猶豫多疑（初始信任度 45）', 3: '犀利挑剔（初始信任度 25）' };
  const DIFF_OPTS = [
    [1, '溫和友善', '初始信任度 60｜適合新人練習，願意傾聽理專引導'],
    [2, '猶豫多疑', '初始信任度 45｜一般實戰難度，對收益與風險反覆提問確認'],
    [3, '犀利挑剔', '初始信任度 25｜高階抗壓對抗，容易打斷反駁、對話銳利'],
  ];
  const TRUST = { 1: 60, 2: 45, 3: 25 };

  // 每種人設的常見異議：[客戶異議, 背後心理, 得分要點, 地雷陷阱]
  const OBJ = {
    owner: [
      ['我以前買過賠很慘，你怎麼保證這次不會？', '創傷未癒，把「賠過」投射到所有商品', '不保證收益、坦承風險、配置比重與可承受損失', '說「這次不一樣」「一定不會賠」'],
      ['發行機構倒閉我的錢怎麼辦？', '最怕血本無歸', '信用風險、評等不等於不違約、非存款保險、分散配置', '保證不會倒、大公司不可能出問題'],
      ['20年期太長，跌下來誰賠？', '怕被套牢、沒人負責', '期間、價格波動、市價賣出、只放閒置資金', '迴避「誰賠」的問題'],
      ['我寧願放定存，至少不會少。', '安全感需求', '不與定存比較配息，回到資金目標與風險承受度', '拿配息和定存比較'],
      ['你們賣出去就不管我了吧？', '怕被棄置', '售後追蹤、市價通知、定期檢視', '承諾無法兌現的服務'],
      ['最壞情況會賠多少？', '想看見底線', '以情境說明可能損失範圍、不保證', '只講好情況'],
    ],
    eng: [
      ['這跟ETF比差在哪？', '自己有做功課，要被說服', '客觀比較費用、流動性、收益來源與風險', '貶低ETF、隱瞞費用'],
      ['你說流動性好，想賣時價格很爛怎麼辦？', '怕被套牢、不能彈性運用', '坦承價差與流動性風險、適當持有期間', '說「隨時可原價賣出」'],
      ['提前買回我不就等於被佔便宜？', '怕條款對自己不利', '提前贖回條款原文、再投資風險', '保證不會被贖回'],
      ['我自己買直債手續費更低吧？', '比價心態', '透明說明費用結構與服務差異', '隱瞞交易成本'],
      ['20年期太長，利率升了我不就套牢？', '怕利率風險', '利率與價格反向、以數字舉例', '迴避價格下跌可能'],
      ['主動基金績效也不穩定吧？', '質疑績效', '過去績效不代表未來，資料完整揭露', '以過去績效當保證'],
    ],
    senior: [
      ['你講白話一點，我聽不懂這些。', '害怕被唬弄、面子', '生活化比喻、每講一點就確認是否聽懂', '連續使用英文縮寫與術語'],
      ['沒賺少賺沒關係，但賠錢出去很沒面子捏。', '怕賠錢失面子', '同理情緒、不保證、只放閒置資金', '說「不會賠」「很穩」'],
      ['每個月要讓我看到錢，可以嗎？', '要穩定現金流', '說明付息／配息頻率但不保證金額', '保證每期領得到'],
      ['這個跟定存有什麼不一樣？', '拿熟悉的東西比較', '清楚區分非存款保險，不以配息比定存', '說「跟定存一樣安全」'],
      ['我兒子說這種東西很危險。', '需要家人認同', '邀請家人一同了解、尊重並回應疑慮', '貶低家人意見'],
      ['我老了，20年我等不到吧？', '擔心年期與傳承', '適配性：年齡、年期與資金用途、高齡客戶保護', '為成交忽略適合度'],
    ],
  };

  // 人設卡。前面幾欄照生成器；後面是轉成對練情境時用的：頭像、個性、沒被問到不會說的事、問到才說、接起電話的第一句
  const PERSONAS = [
    {
      id: 'owner', title: '保守賠過錢企業主', sub: '許董／陳老闆 45-50歲', name: '許董', age: '48', job: '中小企業負責人', asset: '可投資資產約 3,000 萬',
      pain: '曾有投資重大虧損，深怕資產再縮水，對長年期與發行機構倒閉風險極度戒備', axis: '信用風險說明、資金配置比重與停損機制',
      av: 'chen', axisShort: '信用風險與配置比重',
      style: '你做生意很實際、講話直接，最在意本金安全；聽到「很穩」「不會賠」這種話反而更不信任。',
      hidden: '這筆是公司的閒錢，三到五年內不會動用。帳面波動最多能接受一成，再多會睡不著。以前買過商品，想賣的時候價格很差、根本賣不掉。',
      reveal: ['業務問這筆錢多久不會用到 → 回答：公司的閒錢，三五年應該不會動。', '業務問能接受多少波動 → 回答：我最多接受一成吧，再多我晚上會睡不著。', '業務問以前的投資經驗 → 回答：以前買過東西，想賣的時候價格爛死根本賣不掉，賠很慘。'],
      open: ['喂，{業務稱呼}你好。要介紹商品喔？好啊，你說說看，不過講白話一點。', '喂，{業務稱呼}喔，什麼事？又要介紹商品嗎？我先講，我以前買過賠很慘，你講白話一點。'],
    },
    {
      id: 'eng', title: '精明上班族', sub: '陳工程師 35歲', name: '陳工程師', age: '35', job: '科技業工程師', asset: '可投資資產約 500 萬',
      pain: '重視資金彈性與流動性，質疑年期太長、被提前買回吃虧，習慣拿直債、主動基金與 ETF 比較', axis: '流動性與提前買回機制、與 ETF／直債的差異比較',
      av: 'chen', axisShort: '流動性與提前買回',
      style: '你自己有做功課，習慣拿 ETF、直債和基金比較；討厭話術，喜歡數字和白紙黑字。',
      hidden: '可以投資的錢裡大概三成是閒錢，其他要保持彈性。最在意流動性，想賣的時候要賣得掉。自己已經有買 ETF 和美債。',
      reveal: ['業務問資金預計放多久、有沒有要用 → 回答：大概三成是閒錢，其他要保持彈性。', '業務問比較在意現金流還是價格彈性 → 回答：我比較在意流動性，想賣的時候要賣得掉。', '業務問現在的投資 → 回答：我自己有買 ETF 跟美債。'],
      open: ['喂，{業務稱呼}你好。有新商品喔？可以啊，你講重點，我自己也有做功課。', '喂，{業務稱呼}你好。你們打來通常就是要賣東西吧？我自己有買 ETF 跟美債，你直接講重點。'],
    },
    {
      id: 'senior', title: '高齡退休領息族', sub: '張媽媽 65歲', name: '張媽媽', age: '65', job: '退休教師', asset: '退休金約 800 萬',
      pain: '極度怕賠錢失面子，只要穩定現金流，抗拒複雜條款', axis: '白話解說條款、現金流穩定度與風險適配',
      av: 'lin', axisShort: '會不會賠、條款聽不懂',
      style: '你聽不懂專有名詞，怕被唬弄也怕丟面子；講話比較慢，常常提到兒子的意見。',
      hidden: '這是退休金的一部分，大約三成，不急著用。每個月想看到有錢進來。兒子說不要全部放在一起，也覺得這種商品很危險。以前被騙過，賠錢覺得很沒面子。',
      reveal: ['業務問這筆錢占退休金多少、急不急著用 → 回答：退休金的一部分，大概三成吧，不急著用。', '業務問想要怎麼領 → 回答：我每個月要讓我看到錢啦。', '業務問家人的想法 → 回答：我兒子說不要全部放在一起，他說這種東西很危險。'],
      open: ['喂～{業務稱呼}喔？好啊你慢慢講，不過要講白話一點，我比較聽不懂。', '喂～{業務稱呼}喔？我又聽不懂那些啦，你要講就講白話一點，我以前被騙過捏。'],
    },
  ];

  // 超業範本對話（理專主動來電／約訪，客戶被動戒備）。{N} 客戶稱呼、{PT} 商品名稱、{ISS} 發行機構、{RATING} 評等、{CPN} 票息、{TEN} 期間
  const PD = {
    owner: {
      open: '{N}您好！我是永豐金證券的超業小豐。打擾您一下，最近股市在高檔震盪，美國降息循環剛啟動，很多老闆都在問資金要往哪裡放比較安心。今天特別跟您報告一檔由永豐獨賣的「{PT}」，我三分鐘跟您講重點就好，可以嗎？',
      c1: '又是推銷喔？我先跟你講，我以前買過東西，想賣的時候價格爛死根本賣不掉，所以我很怕這種。你講白話一點啦。',
      emp: '{N}，謝謝您直接跟我說，這種經驗真的會讓人很不舒服，換成我也會警戒。所以我今天不會叫您買，只是先把條件跟風險講清楚，您聽完覺得不適合，我們就不談，好不好？',
      needQ: '那我先請教兩件事，才不會浪費您時間：這筆資金大概幾年內不會動用？還有，如果帳面短期出現波動，您心裡可以接受大概多少幅度？',
      needA: '公司的閒錢，三五年應該不會動。波動……我最多接受一成吧，再多我晚上會睡不著。',
      p1c: '那你講的這個發行機構如果倒閉，我的錢不就沒了？大公司倒的我又不是沒看過。',
      p1a: '{N}您問到重點了。發行機構如果發生違約，本金確實可能損失，所以這不是保本商品，也不是存款保險的標的。這檔的發行機構是{ISS}，信用評等是{RATING}，但評等好不代表一定不會出事。所以我建議只拿您能承擔波動的那一部分資金，而且分散配置，不把雞蛋放在同一個籃子裡。',
      hes: '嗯……聽起來你有講實話，不像以前那些人只會說很穩。但我還是怕，我回去想一下。',
    },
    eng: {
      open: '{N}您好！我是永豐金證券的超業小豐。最近美債殖利率在高檔震盪，降息循環也剛啟動，有不少客戶在調整債券部位。我手上有一檔由永豐獨賣的「{PT}」，想先跟您報告一下條件，您現在方便三分鐘嗎？',
      c1: '你們這種來電通常就是要賣東西吧？我自己有買ETF跟美債，你要說服我為什麼要買這個。',
      emp: '完全理解，您自己有做功課，我也不會用業務話術帶過。我會把收益、費用、風險，還有跟ETF的差異都講清楚，您覺得不划算就直說，我不介意。',
      needQ: '那我先確認兩件事：這筆資金預期持有多久？半年到一年內會不會有用途？還有，您比較在意固定現金流，還是價格的彈性？',
      needA: '大概三成是閒錢，其他要保持彈性。我比較在意流動性，想賣的時候要賣得掉。',
      p1c: '這跟ETF比差在哪？還有，你說流動性好，想賣時價格很爛怎麼辦？',
      p1a: '您問得很準。ETF透明度高、盤中可交易、費用也低，這是優點。這檔的差異在於條件是事先約定的：{CPN}，期間是{TEN}；但相對的，流動性與買賣價差風險也比較明顯，賣出是依市價成交，可能低於您的成本。所以它比較適合不急用的資金。我可以把兩者的費用、流動性、價格波動做成比較表給您，不一定要二選一。',
      hes: '你講得蠻透明的。我先拿三成資金評估，再回覆你。',
    },
    senior: {
      open: '{N}您好！我是永豐金證券的超業小豐。最近股市在高檔震盪，降息也開始了，很多長輩都在問有沒有可以定期領錢的地方。今天特別跟您報告一檔由永豐獨賣的「{PT}」，我慢慢講，不會很久，可以嗎？',
      c1: '小姐啊，我又聽不懂這些。你講白話一點，我以前被騙過，賠錢很沒面子捏。',
      emp: '{N}，您的擔心我懂，面子跟安全感都很重要。我不會講一堆專有名詞，也不會叫您一定要買，您聽得懂我再往下講，聽不懂就隨時打斷我，好不好？',
      needQ: '那我想先請教您：每個月的生活費大概需要多少？這筆錢是退休金裡的多少比例？是不是不急著用的錢？',
      needA: '退休金的一部分，大概三成吧。我每個月要讓我看到錢啦，我兒子說不要全部放在一起。',
      p1c: '沒賺少賺沒關係，但賠錢出去很沒面子捏。你可以保證不會賠嗎？',
      p1a: '{N}，我不能跟您保證，這是老實話。這個商品不是存款，也不保本，價格可能上下波動。所以我只建議放您不急著用的那三成，不會勸您全部買。風險我等一下一項一項念給您聽，您有聽不懂的隨時打斷我，也可以請兒子一起看這份說明。',
      hes: '好啦，你講得很清楚，我跟我兒子商量一下。',
    },
  };

  const ROUND_TIPS = {
    adv: { 同理破冰: '主動破冰：掌握市場話題、說明來意並徵詢時間；嚴禁「歡迎光臨、今天想了解什麼」', 需求探詢: '以開放式提問做 KYC：資金期間、波動承受度、過往經驗', 白話說明: '白話比喻＋主動且完整揭露必揭露風險，不連續講英文縮寫', 痛點迎擊: '先同理、再回應；不保證、不貶低他類、不與定存比較', 促成下一步: '重述非存款／不保證收益與交易規則，不催促，約定下一步' },
    cust: { 同理破冰: '客戶心理：被推銷的戒備，自然帶出口頭禪與過往創傷', 需求探詢: '客戶透露資金性質與底線，是適配性分析的依據', 白話說明: '客戶要求白話，檢驗業務員解說能力', 痛點迎擊: '核心刁難：檢驗風險揭露、異議處理與合規用語', 促成下一步: '客戶猶豫：不硬推，給客戶決定空間' },
  };

  // 第一個痛點（看人設）、第二個痛點（看商品類型）：[得分要點, 地雷]，以及轉成對練情境時的檢核項目和示範句
  const P1K = {
    owner: { k: ['信用／違約風險、評等不等於不違約、配置比重與分散', '保證不會倒、大公司不可能出問題'], label: '回應信用與違約的疑慮', hint: '評等不等於不違約、非存款保險；建議分散、控制比重' },
    eng: { k: ['與 ETF 的客觀比較、流動性與買賣價差、費用透明', '貶低 ETF、隱瞞價差、說隨時可原價賣出'], label: '客觀比較 ETF 與流動性', hint: '費用、流動性、買賣價差照實說明，不貶低 ETF' },
    senior: { k: ['不保證、白話、只放閒置資金、邀請家人一同了解', '保證不會賠、連續使用專有名詞'], label: '不保證並同理怕賠的心情', hint: '不說不會賠；只放閒置資金，邀請家人一起了解' },
  };
  // 「保守賠過錢企業主」的第一個痛點是照債券寫的（發行機構倒閉、信用評等）；基金沒有這些，換成怕淨值一直跌
  const P1K_FUND_OWNER = { k: ['淨值波動與本金虧損、非存款保險、過去績效不代表未來、配置比重與分散', '保證不會賠、說很穩、拿過去績效當保證'], label: '回應怕再虧損的疑慮', hint: '淨值會波動、非保本、非存款保險；建議分散、控制比重' };
  const P2K = {
    fcn: { k: ['到期跌破履約價可能承接標的、非保本、最壞損失本金、提前出場與再投資', '只講固定配息、說保本或穩領息、淡化承接標的的風險'], label: '講清跌破時的承接風險', hint: '到期跌破履約價可能承接最差標的；非保本，最壞損失本金', demo: '配息是固定的，但不保本；到期若標的跌破履約價，可能要承接股票，本金會虧損。' },
    bond: { k: ['提前贖回條款原文、再投資風險、不保證是否被贖回', '保證不會被贖回、只講前段高息'], label: '講清提前贖回與再投資', hint: '條款照實說；被買回後利率不一定一樣好，不保證會不會被贖回', demo: '如果被提前買回，您會拿回本金，但之後的利率不一定一樣好，這就是再投資風險。' },
    fund: { k: ['配息可能來自本金、看總報酬、金額不保證', '承諾固定配息、拿配息比定存'], label: '講清配息可能來自本金', hint: '配息不保證；要一起看淨值走勢和總報酬', demo: '配息不是保證的，也可能由本金支付，所以要一起看淨值走勢和總報酬。' },
    custom: { k: ['列出主要風險、先確認適配度', '淡化風險、忽略適合度'], label: '說明主要風險與最壞情況', hint: '最壞可能損失本金；先確認適不適合，再談金額', demo: '最壞的情況可能損失本金，所以我會先確認它適不適合您，再談金額。' },
  };

  // 100 分制評分指標：[面向, 項目, 滿分, 代號, 亮點, 可加強]（和 App 的評分表是同一套）
  const ITEMS = [
    ['產品面（40%）', '商品結構掌握度', 5, 'struct', '白話說明發行機構、收益條件、期間與額外條件', '可再補充商品結構圖示'],
    ['', '風險揭露完整性', 7, 'risk', '必揭露風險全數於話術中主動、完整說明', '部分必揭露風險未於話術涵蓋'],
    ['', '產品優勢表達能力', 7, 'adv', '以白話呈現收益條件，同時不誇大', '可再加入貼近客戶情境的優勢說明'],
    ['', '商品適配性分析', 7, 'fit', '先 KYC（期間、波動承受度）再說明適配', '—'],
    ['', '同類比較分析能力', 7, 'cmp', '客觀比較同類商品屬性，不貶低他類', '可再補充與同類商品的數據比較'],
    ['', '使用案例舉例', 7, 'case', '以客戶資金比例與生活情境舉例', '可再補充數字試算（須註明不保證）'],
    ['銷售面（40%）', '開場與建立信任', 7, 'open', '理專主動破冰、掌握市場話題並徵詢時間', '—'],
    ['', '需求探索能力', 7, 'need', '開放式提問確認資金期間與波動承受度', '—'],
    ['', '異議處理能力', 7, 'obj', '先同理、後回應；刁難輪次皆正面迎擊且不保證', '—'],
    ['', '成交推進力道', 7, 'close', '重點整理後不催促、約定下一次聯絡', '可再明確提出下一步行動選項'],
    ['', '風險評估平衡', 7, 'bal', '收益與風險並重，建議只放閒置資金與可承擔比例', '—'],
    ['', '專業形象與同理心', 5, 'emp', '傾聽、同理客戶情緒，語氣專業', '—'],
    ['合規面（20%）', '銷售用語合規性', 10, 'lang', '全程無保證收益、誇大保本、配息比定存、借貸投資等用語', '偵測到疑似紅線用語，請修正'],
    ['', '銷售交易規則', 10, 'rule', '說明期間／閉鎖、申購方式與交付文件', '交易規則或交付文件說明不足'],
  ];
  const STRONG = ['obj', 'risk', 'lang', 'need', 'open', 'fit', 'bal'];
  const STRONG_TXT = { obj: '【異議處理能力】先同理再迎擊，每個刁難皆有完整示範', risk: '【風險揭露完整性】必揭露風險逐項主動說明', lang: '【銷售用語合規性】全程無紅線用語、明確不保證', need: '【需求探索能力】以 KYC 問句取得資金期間與波動底線', open: '【開場與建立信任】理專主動破冰，不說「歡迎光臨」', fit: '【商品適配性分析】依客戶資金性質調整配置建議', bal: '【風險評估平衡】收益與風險並重' };
  const SECTIONS = [['s1', '一、對練背景'], ['s2', '二、最佳實戰對話範本【超業怎麼說】'], ['s3', '三、對練要點提醒 ＆ 核心異議攻防'], ['s4', '四、自評與 100 分制評分指標']];

  // ------------------------------------------------------------------ 純邏輯

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const v = (x, fb) => String(x || '').trim() || fb;
  // 使用者填的字：拿掉大括號（存進情境時大括號是「代稱」專用）和控制字元
  const tidy = (s) => String(s ?? '').replace(/[{}]/g, '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '');
  const clip = (s, n) => { const t = String(s ?? '').trim(); return t.length > n ? `${t.slice(0, n - 1).trimEnd()}…` : t; };
  const one = (s, n) => clip(tidy(s).replace(/\s+/g, ' '), n); // 單行
  const many = (s, n) => clip(tidy(s).replace(/\r\n?/g, '\n').replace(/\n{3,}/g, '\n\n'), n); // 多行
  const persona = (id) => PERSONAS.find((p) => p.id === id) || PERSONAS[0];
  // 人設卡的痛點和攻防重點原本是照債券寫的（長年期、發行機構倒閉、提前買回、直債）：基金和 FCN 換成自己的說法
  const PERSONA_BY_TYPE = {
    fund: {
      owner: { pain: '曾有投資重大虧損，深怕資產再縮水，對淨值波動和本金虧損極度戒備', axis: '淨值波動與虧損風險的說明、資金配置比重與停損機制', axisShort: '虧損風險與配置比重' },
      eng: { pain: '重視資金彈性與流動性，在意閉鎖期、贖回限制和費用，習慣拿 ETF 和其他基金比較', axis: '流動性與贖回限制、與 ETF 的差異比較', axisShort: '流動性與 ETF 比較', style: '你自己有做功課，習慣拿 ETF 和其他基金比較；討厭話術，喜歡數字和白紙黑字。' },
    },
    fcn: {
      owner: { pain: '曾有投資重大虧損，深怕資產再縮水，對本金虧損和發行機構倒閉風險極度戒備', axis: '本金損失與信用風險的說明、資金配置比重', axisShort: '本金損失與信用風險' },
      eng: { pain: '重視資金彈性與流動性，質疑提前出場吃虧、跌破要承接標的，習慣拿 ETF 和直接買股票比較', axis: '提前出場與承接標的的條件、與 ETF／直接買股票的差異比較', axisShort: '提前出場與承接條件', style: '你自己有做功課，習慣拿 ETF 和直接買股票比較；討厭話術，喜歡數字和白紙黑字。' },
    },
  };
  const personaFor = (type, id) => { const b = persona(id); return { ...b, ...((PERSONA_BY_TYPE[type] || {})[b.id] || {}) }; };
  // 常見異議和預設口頭禪裡，只有債券才會講的話（基金沒有發行機構倒閉、20 年期、提前買回；FCN 沒有 20 年期）
  const BOND_ONLY = { fund: /發行機構|20\s*年|提前買回|直債/, fcn: /20\s*年/ };
  const fitsType = (type, s) => !(BOND_ONLY[type] && BOND_ONLY[type].test(String(s)));
  const objOf = (ctx) => OBJ[ctx.pr.id].filter((o) => fitsType(ctx.type, o[0]));

  // 把畫面上（或情境檔裡）的內容整理成固定的樣子：缺的補人設卡的預設、太長的截掉
  function normalize(raw) {
    raw = raw && typeof raw === 'object' ? raw : {};
    const type = TYPES[raw.type] ? raw.type : 'bond';
    const pr = personaFor(type, raw.pid);
    const p0 = raw.p && typeof raw.p === 'object' ? raw.p : {};
    const p = {};
    FK.forEach((k) => { p[k] = k === 'risks' ? many(p0[k], 1200) : one(p0[k], k === 'name' ? 60 : 200); });
    if (!/^RR[1-5]$/.test(p.risk)) p.risk = '';
    const used = new Set(fieldsOf(type).map(([k]) => k));
    FK.forEach((k) => { if (!used.has(k)) p[k] = ''; }); // 這個類型用不到的欄位：清空，換過類型後留下來的舊內容才不會混進去
    const c0 = raw.c && typeof raw.c === 'object' ? raw.c : {};
    const age = Math.max(18, Math.min(95, parseInt(c0.age, 10) || parseInt(pr.age, 10)));
    const c = { name: one(c0.name, 12) || pr.name, age, job: one(c0.job, 30) || pr.job, asset: one(c0.asset, 40) || pr.asset };
    const ph = [...new Set((Array.isArray(raw.ph) ? raw.ph : []).map((x) => one(x, 40)).filter(Boolean))].slice(0, 8);
    const d = [1, 2, 3].includes(Number(raw.d)) ? Number(raw.d) : 2;
    const av = ['chen', 'lin', 'wang'].includes(raw.av) ? raw.av : pr.av;
    const w0 = raw.w && typeof raw.w === 'object' ? raw.w : {};
    return { type, pid: pr.id, pr, p, c, ph, d, av, w: { unit: one(w0.unit, 20), name: one(w0.name, 20) } };
  }

  function riskList(ctx) {
    const l = ctx.p.risks.split('\n').map((x) => x.replace(/^\s*\d+[.、)]\s*/, '').trim()).filter(Boolean);
    return l.length ? l : RISK_DEF[ctx.type];
  }

  // 劇本情境大綱：三段，每段幾行字（確認時可以改）
  function buildOutline(ctx) {
    const { p, c, pr, ph, d, type } = ctx;
    // 商品那一段照這個類型的欄位列出來（基金不會出現「債券信用評等」「提前贖回」這些債券才有的項目）
    const prod = [`商品：【${TYPES[type]}】${p.name}`]
      .concat(fieldsOf(type).filter(([k]) => k !== 'name' && k !== 'risks' && p[k]).map(([k, label]) => `${label}：${p[k]}`))
      .concat(`必揭露風險：${riskList(ctx).join('；')}`);
    const cust = [`客戶：「${c.name}」，${c.age}歲，${c.job}，${c.asset}`, `類型：${pr.title}`, `核心痛點：${pr.pain}`,
      ph.length && `口頭禪：${ph.map((s) => `「${s}」`).join('')}`, `刁難度：${DIFFS[d]}`, '情境：由理專主動來電／約訪，客戶被動且帶戒備'].filter(Boolean);
    const ax = type === 'fcn' ? '連結標的下跌時的承接與本金損失是否講清楚' : type === 'bond' ? '提前贖回與再投資風險是否講清楚' : type === 'fund' ? '配息可能來自本金是否講清楚' : '主要風險是否完整揭露';
    const atk = [`核心攻防：「${pr.axis}」`, `本次關鍵刁難：${ax}`, '理專須先同理、後探詢，再以白話說明商品與風險', '合規底線：不保證收益、不誇大保本、不以配息比定存、不建議借貸投資，成交前說明交易規則'];
    return { prod, cust, atk };
  }

  function cleanOutline(ol, ctx) {
    const base = buildOutline(ctx);
    const pick = (k) => {
      const a = Array.isArray(ol?.[k]) ? ol[k].map((x) => one(x, 400)).filter(Boolean).slice(0, 14) : [];
      return a.length ? a : base[k];
    };
    return { prod: pick('prod'), cust: pick('cust'), atk: pick('atk') };
  }

  // 第二個痛點的問答和白話說明，依商品類型
  function typeLines(ctx) {
    const { p, type } = ctx;
    const rs = riskList(ctx);
    const extra = p.extra ? `另外有一個額外條件要先說明：「${p.extra}」。` : '';
    if (type === 'fcn') {
      return {
        mech: `我用白話講：FCN 是一種連結標的的結構型商品，發行機構是${v(p.issuer, '發行機構')}，連結的標的是「${v(p.under, '依商品說明書')}」。約定的配息是「${v(p.coupon, '依商品說明書')}」，天期是「${v(p.tenor, '依商品說明書')}」。${extra}重點是：配息是固定的，但本金不保本；到期能不能拿回全部本金，要看連結標的的價格和發行機構的信用。`,
        p2c: '配息固定聽起來不錯，但如果連結的標的大跌，我到期會拿到什麼？會不會變成一堆股票？',
        p2a: `這點一定要講清楚：到期時如果連結標的跌破約定的價格（履約價「${v(p.strike, '依商品說明書')}」${p.ki ? `、下限價「${p.ki}」` : ''}），您拿回來的可能不是現金，而是要用履約價承接表現最差的那一檔標的，這時候本金就會虧損，最壞可能損失全部本金。另外如果達到提前出場條件「${v(p.call, '依商品說明書')}」，商品會提前結束、拿回本金，之後要重新找標的。哪一種情況會發生我不能保證，所以請只用您能承擔這種結果的資金。`,
      };
    }
    if (type === 'bond') {
      return {
        mech: `我用白話講：就是您把錢借給${v(p.issuer, '發行機構')}，對方約定「${v(p.coupon, '依商品說明書')}」，期間是「${v(p.tenor, '依商品說明書')}」，到期再還本金。${extra}重點是，能不能如期付息還本，要看發行機構的信用。`,
        p2c: '還有，這種前面利息比較高的，提前買回我不就等於被佔便宜？',
        p2a: `您擔心的有道理，所以我先把條款講清楚：「${v(p.call, '依商品說明書')}」。如果被提前買回，您會拿回本金，但之後要重新找標的，利率不一定一樣好，這就是再投資風險。而且會不會被買回，取決於發行機構與市場利率，我不能保證會或不會。我不會只跟您講前面的好處。`,
      };
    }
    if (type === 'fund') {
      return {
        mech: `我用白話講：基金就是把很多人的錢交給專業經理人，分散投資一籃子資產。配息條件是「${v(p.coupon, '依公開說明書')}」，期間與交易規則是「${v(p.tenor, '依公開說明書')}」，淨值會隨市場漲跌。${extra}`,
        p2c: '那配息是不是都是賺來的錢？我每個月要看到錢，會不會配到本金？',
        p2a: '這點一定要講清楚：配息不是保證的，也可能由本金支付，所以高配息不代表高報酬，要一起看淨值走勢跟總報酬。我們會依您的現金流需求試算，但金額不是保證，我也不會跟您承諾每個月一定領多少。',
      };
    }
    return {
      mech: `我用白話講「${v(p.name, '這個商品')}」：收益條件是「${v(p.coupon, '依商品說明書')}」，期間是「${v(p.tenor, '依商品說明書')}」。${extra}我先講結構，再講風險。`,
      p2c: '這個商品最大的風險到底是什麼？最壞會怎樣？',
      p2a: `主要風險有：${rs.slice(0, 3).join('；')}。最壞情況可能損失本金，所以我會先確認它適不適合您，再談金額，而且建議控制在可承擔的比例。`,
    };
  }

  const fill = (ctx) => (s) => s.replace(/\{N\}/g, ctx.c.name).replace(/\{PT\}/g, v(ctx.p.name, '這檔商品')).replace(/\{ISS\}/g, v(ctx.p.issuer, '發行機構'))
    .replace(/\{RATING\}/g, v(ctx.p.rating, '依商品說明書')).replace(/\{CPN\}/g, v(ctx.p.coupon, '依商品說明書')).replace(/\{TEN\}/g, v(ctx.p.tenor, '依商品說明書'));

  // 範本對話大多跟商品類型無關，少數幾句是照債券寫的：其他類型換一個說法
  function linesOf(ctx) {
    const D = { ...PD[ctx.pr.id] };
    if (ctx.type !== 'bond' && ctx.pr.id === 'eng') {
      D.open = D.open.replace('最近美債殖利率在高檔震盪，降息循環也剛啟動，有不少客戶在調整債券部位。', '最近市場在高檔震盪，降息循環也剛啟動，有不少客戶在重新調整配置。');
    }
    if (ctx.type === 'fund' && ctx.pr.id === 'owner') {
      D.p1c = '那基金如果一直跌，我的錢不就越來越少？以前賠過，我很怕。';
      D.p1a = '{N}您問到重點了。基金的淨值會跟著市場漲跌，本金確實可能虧損，所以這不是保本商品，也不是存款保險的標的，過去績效也不代表未來。所以我建議只拿您能承擔波動的那一部分資金，而且分散配置，不把雞蛋放在同一個籃子裡。';
    }
    return D;
  }
  const firstPain = (ctx) => (ctx.type === 'fund' && ctx.pr.id === 'owner' ? P1K_FUND_OWNER : P1K[ctx.pr.id]);

  // 超業範本對話：六輪。回傳一列一列：{ m: 1, s: 階段, l: 標題 } 是每一輪的標題，{ s, w: 'A' 超業 | 'C' 客戶, t: 內容 } 是一句話
  function buildChat(ctx) {
    const { p, pr, ph, d } = ctx;
    const D = linesOf(ctx);
    const F = fill(ctx);
    const rs = riskList(ctx);
    const { mech, p2c, p2a } = typeLines(ctx);
    const wp = (t, i) => (ph[i] && !t.includes(ph[i]) ? `「${ph[i]}」……${t}` : t);
    const rows = [];
    let round = 0;
    const add = (s, w, t) => rows.push({ s, w, t: clip(t, 900) }); // 和 cleanRows 一樣最多 900 字，存進去和讀回來才會一樣
    const start = (s) => { round += 1; rows.push({ m: 1, s, l: `【第 ${round} 輪｜${s}】` }); };
    start('同理破冰');
    add('同理破冰', 'A', F(D.open));
    add('同理破冰', 'C', (d === 1 ? '喔，好啊，你說說看。' : '') + wp(F(D.c1), 0));
    add('同理破冰', 'A', F(D.emp));
    start('需求探詢');
    add('需求探詢', 'A', F(D.needQ));
    add('需求探詢', 'C', D.needA);
    add('需求探詢', 'A', '好，我聽懂了，謝謝您願意跟我說。這樣我知道怎麼幫您評估，等一下我會依您剛剛講的期間和波動承受度，看這檔適不適合您。');
    start('白話說明');
    add('白話說明', 'A', mech);
    add('白話說明', 'C', '所以我實際上會拿到什麼？大概怎麼算？你講白話一點。');
    add('白話說明', 'A', `我換成您聽得懂的講法：約定的條件是「${v(p.coupon, '依商品說明書')}」，實際金額依條件計算，不是保證，我稍後會把試算表一併給您。接下來我把必須讓您知道的風險一項一項講：${rs.join('；')}。這不是存款，沒有保證收益，可能損失本金，您有任何一項聽不懂，我再用白話講一次。`);
    start('痛點迎擊');
    add('痛點迎擊', 'C', wp(F(D.p1c), 1));
    add('痛點迎擊', 'A', F(D.p1a));
    if (d === 3) {
      add('痛點迎擊', 'C', '你們每個人都這樣講，賣東西當然說好聽，我怎麼信你？');
      add('痛點迎擊', 'A', '您的警覺是對的。我不會保證任何結果，您可以對照公開說明書與風險預告書自己確認；如果評估下來不適合您，我也會直接告訴您不建議。');
    }
    start('痛點迎擊');
    add('痛點迎擊', 'C', wp(p2c, 2));
    add('痛點迎擊', 'A', p2a);
    start('促成下一步');
    add('促成下一步', 'C', F(D.hes) + (ph[3] && !D.hes.includes(ph[3]) ? `（${ph[3]}……）` : ''));
    add('促成下一步', 'A', `當然可以，這是很大的決定，不需要今天決定。我幫您重點整理一次：第一，這不是存款、沒有保證收益、可能損失本金；第二，期間與條件是「${v(p.tenor, '請見說明書')}」${p.extra ? `，加上「${p.extra}」` : ''}；第三，建議只用不急用的閒置資金，控制在您可承擔的比例。`);
    add('促成下一步', 'C', '好，那你把資料給我，我回去看。');
    add('促成下一步', 'A', '沒問題。申購方式與要簽署的文件，包括商品說明書和風險預告書，我會一併寄給您，您也可以請家人一起看。我週四下午再跟您聯絡，到時候您有疑問我們再談，如果您想進一步，我再重新確認您的風險屬性。謝謝您的時間！');
    return rows;
  }

  function cleanRows(rows, ctx) {
    const ok = Array.isArray(rows) ? rows.filter((r) => r && typeof r === 'object').slice(0, 60).map((r) => (r.m
      ? { m: 1, s: one(r.s, 20), l: one(r.l, 40) }
      : { s: one(r.s, 20), w: r.w === 'A' ? 'A' : 'C', t: many(r.t, 900) })).filter((r) => r.m || r.t) : [];
    return ok.some((r) => !r.m) ? ok : buildChat(ctx);
  }

  // 三步驟填的東西 → App 的對練情境（欄位和長度限制照 server.py 的 sanitize_scenario）
  function toScenario(ctx, ol, rows) {
    const { p, c, pr, ph, d, type } = ctx;
    const D = linesOf(ctx);
    const F = fill(ctx);
    const pname = v(p.name, '這檔商品');
    const risks = riskList(ctx);
    const first = firstPain(ctx);
    const second = P2K[type];
    const { p2c } = typeLines(ctx);
    const guard = d === 1 ? 0 : 1;
    const facts = [`【商品】${pname}（${TYPES[type]}）`]
      .concat(fieldsOf(type).filter(([k]) => k !== 'name' && k !== 'risks' && p[k]).map(([k, label]) => `${label}：${p[k]}`))
      .concat('【必揭露風險】', risks.map((r, i) => `${i + 1}. ${r}`)).join('\n');
    const attitude = { 1: '你態度友善，願意聽對方說明。', 2: '你半信半疑，對收益和風險會反覆確認。', 3: '你說話犀利、容易打斷對方，一聽到含糊的回答就反駁。' }[d];
    const personaText = `你是${c.name}，${c.age} 歲，${c.job}，${c.asset}。${pr.pain}。你的帳戶開在{公司}，{職稱簡稱}{業務稱呼}主動打電話來向你介紹「${pname}」。`
      + `${pr.style}${ph.length ? `你的口頭禪：${ph.map((s) => `「${s}」`).join('')}，對話中自然地講出來。` : ''}${attitude}`;
    const behavior = [
      `對方說明來意後，你先表達戒心：「${F(D.c1)}」`,
      '對方說明商品後，你會提出下面這些質疑，一次只問一個，不要一口氣講完：',
      `- ${F(D.p1c)}`, `- ${p2c}`,
      d === 3 ? '- 你們每個人都這樣講，賣東西當然說好聽，我怎麼信你？' : '',
      `- 其他可能會問的：${objOf(ctx).slice(0, 3).map((o) => o[0]).join(' ')}`,
      '做對時：業務先同理你、坦白講風險、不保證、回答具體，你會慢慢放心。',
      '做錯時：業務保證獲利、說不會賠、迴避問題、拿配息比定存，或催你今天決定，你會更不信任，直接說「你們都這樣講」。',
      `結尾：你不會當場決定，會說「${F(D.hes)}」`,
    ].filter(Boolean).join('\n');
    const coach = [
      '好的流程：主動破冰並徵詢時間 → 用開放式問題做 KYC（資金期間、波動承受度、過往經驗）→ 白話說明條件與額外條件 → 逐項揭露必揭露風險 → 先同理再回應質疑 → 不催促並約定下一步。',
      `得分要點：${first.k[0]}；${second.k[0]}。`,
      `地雷：${first.k[1]}；${second.k[1]}；保證收益、誇大保本、以配息比定存、建議借貸投資、沒有說明交易規則與交付文件。`,
    ].join('\n');
    const product = clip(pname, 30);
    return {
      title: clip(`${pr.title}｜${pname}`, 30),
      product: product.length < 2 ? `${product}商品` : product,
      rubric_type: RUBRIC[type],
      difficulty_level: d,
      duration_min: 8,
      call_direction: 'outgoing',
      initial_trust: TRUST[d],
      customer: {
        name: c.name, age: c.age, job: c.job, avatar: ctx.av,
        summary: clip(`${c.age} 歲${c.job}，${c.asset}。${pr.pain}。{職稱簡稱}主動打電話來介紹商品，客戶一開始有戒心。`, 200),
      },
      learner_task: clip(`主動致電介紹「${clip(pname, 18)}」：先同理、再問清資金期間與波動承受度，白話講清條件並完整揭露風險，正面回應「${pr.axisShort}」的質疑，不催促並約定下一步。`, 120),
      opening_line: pr.open[guard],
      first_tip: '先說明來意、徵詢時間，同理客戶的戒心，別急著推商品。',
      checklist: [
        { id: 'open', label: '主動破冰並徵詢時間', hint: '說明來意、掌握市場話題，先同理客戶的戒心' },
        { id: 'kyc', label: '問資金期間與波動承受度', hint: '開放式提問：多久不會動用、能接受多少波動、過去的投資經驗' },
        { id: 'plain', label: '白話說明商品條件', hint: '收益條件、期間、額外條件；用比喻，不連續講專有名詞' },
        { id: 'risks', label: '完整揭露必揭露風險', hint: '逐項主動說明；不是存款、不保證收益、可能損失本金' },
        { id: 'obj1', label: first.label, hint: first.hint },
        { id: 'obj2', label: second.label, hint: second.hint },
        { id: 'next', label: '不催促並約定下一步', hint: '重述重點、說明申購方式與交付文件，約好下次聯絡時間' },
      ],
      demo_lines: [
        `${c.name}您好，想跟您報告一檔商品的條件和風險，耽誤您三分鐘可以嗎？`,
        '謝謝您直接跟我說，換成我也會警戒。我先把條件和風險講清楚，不適合就不談。',
        '這筆資金大概幾年內不會動用？帳面短期波動，您可以接受多少幅度？',
        '先講最重要的：這不是存款，沒有保證收益，也可能損失本金。',
        second.demo,
        '建議只放不急用的閒置資金，而且控制在您能承擔的比例。',
        '不需要今天決定。資料和風險預告書我寄給您，週四下午再跟您聯絡好嗎？',
        // 最後兩句是違規示範：用字要對得上 App 的合規禁語清單，通話畫面才會標成紅點
        '您放心，這檔很穩，保證不會賠，利息比定存好很多。',
        '這個跟定存一樣安全，放著一定會賺，不用想太多。',
      ],
      server_only: {
        persona: clip(personaText, 1500),
        hidden_info: pr.hidden,
        reveal_rules: pr.reveal.join('\n'),
        behavior: clip(behavior, 1500),
        coach_notes: clip(coach, 1000),
        product_facts: clip(facts, 3000),
      },
      playbook: { v: 1, type, pid: pr.id, av: ctx.av, p: { ...p }, c: { ...c }, ph: [...ph], d, w: { ...ctx.w }, outline: ol, rows },
    };
  }

  // 情境裡存的劇本（或匯入的情境檔）→ 整理好的三樣東西
  function fromPlaybook(pb) {
    const ctx = normalize(pb);
    return { ctx, ol: cleanOutline(pb?.outline, ctx), rows: cleanRows(pb?.rows, ctx) };
  }

  // 只看「填的內容」有沒有變（大綱和對話是另外改的）
  const recipeKey = (ctx) => JSON.stringify([ctx.type, ctx.pid, ctx.av, ctx.p, ctx.c, ctx.ph, ctx.d]);

  // PDF 抽出來的中文常混著「長得一樣、其實是另一個字」的部首字和相容字（例如 ⼆、⾏、⽤）：換回一般的字，後面比對和顯示才正常。
  // 只換這幾段，不動全形標點
  const fixCjk = (s) => String(s || '').replace(/[⺀-⻳⼀-⿕豈-﫿]/g, (c) => c.normalize('NFKC'));

  // 檔名 → 商品名稱：去掉副檔名、下載時多出來的「(1)」「- 複製」，底線換成空白
  const nameFromFile = (fname) => one(String(fname || '').replace(/\.[^.]+$/, '').replace(/[_＿]+/g, ' ')
    .replace(/(?:\s*[（(]\d+[)）]|\s*-\s*(?:複製|副本|copy))+\s*$/i, ''), 60);

  // 換商品類型：預設口頭禪裡這個類型講不通的（例如基金的「20年期太長」）取消勾選；自己加的留著
  function applyType(raw, type) {
    raw.type = type;
    raw.ph = (raw.ph || []).filter((t) => fitsType(type, t) || !PHRASES.includes(t));
  }

  // 文件很長時挑哪幾頁給 AI。有規格字眼的頁面優先：每一組字眼算一次分（同一頁出現很多次也只算一次），再除以頁面長度，
  // 字少而規格多的「基金小檔案」「主要條件」排最前面
  const SPEC_WORDS = [
    [5, /基金小檔案|商品條件|主要條件|發行條件|產品條件|商品摘要|基本資料|Term\s?Sheet|Key\s?Terms/i],
    [4, /連結標的|履約價|下限價|敲入|敲出|提前出場|Knock|Strike|Autocall/i],
    [3, /計價幣別|計價貨幣|幣別|Currency/i],
    [3, /風險(?:收益|報酬)?等級|RR\s?[1-5]/i],
    [2, /ISIN|代號|代碼/i],
    [2, /配息|收益分配|票息|票面利率|Coupon/i],
    [2, /經理費|保管費|手續費|買回|贖回/],
    [2, /到期日|天期|年期|閉鎖期|Maturity|Tenor/i],
    [2, /信用評等|發行機構|保證機構|基金公司|經理公司|Issuer/i],
    [2, /基金類型|基金種類|商品類型/],
  ];
  // 簡報最後面那種落落長的法律聲明（常常是「其他商品」的警語，會讓 AI 搞混）：字多，而且這些套話出現兩種以上；
  // 或是字很多、規格字眼卻很少的長篇說明。這種頁排最後，而且只放第一頁
  const BOILER = [/公開說明書/, /善良管理人/, /不保證最低/, /過去(?:績效|配息)[^，。]{0,6}不代表/, /收益平準金/, /不負責本基金之盈虧/, /免責聲明|Disclaimer/i];
  const specScore = (s) => SPEC_WORDS.reduce((n, [w, re]) => n + (re.test(s) ? w : 0), 0);
  const specDense = (s) => (specScore(s) * 1000) / (s.length + 400);
  const isLegal = (s) => s.length > 1200 && (BOILER.filter((re) => re.test(s)).length >= 2 || specDense(s) < 3);
  const PAGE_CLIP = 3000; // 一頁最多放這麼多字
  const LEGAL_CLIP = 2000;

  // 每一頁的文字 → 要送給 AI 的那一段（最多 budget 個字）。
  // 1. 每頁都有的頁首頁尾（例如「僅供內部使用」）和單獨一行的頁碼先拿掉；2. 放得下就整份都送；
  // 3. 放不下就挑：第一頁一定放，再來是有規格字眼的頁、其他頁，法律聲明只放第一頁；送出去時照原本的頁次排，每頁前面標「【第 N 頁】」
  function pickText(pages, budget) {
    const list = (Array.isArray(pages) ? pages : [pages]).map((s) => String(s || '').replace(/\r\n?/g, '\n'));
    if (list.length <= 1) return (list[0] || '').slice(0, budget); // 只有一頁（Word、txt）：照原樣，太長就截掉
    const lines = list.map((s) => s.split('\n').map((x) => x.trim()).filter(Boolean));
    const seen = new Map();
    lines.forEach((ls) => new Set(ls).forEach((x) => seen.set(x, (seen.get(x) || 0) + 1)));
    const often = Math.max(3, Math.ceil(list.length * 0.4));
    const body = lines.map((ls) => ls.filter((x) => !(x.length >= 6 && seen.get(x) >= often) && !/^\d{1,3}$/.test(x)).join('\n'));
    const mark = (i) => `【第 ${i + 1} 頁】\n`;
    const cost = (i, n) => mark(i).length + n + 1;
    let take = body;
    if (body.reduce((n, s, i) => n + (s ? cost(i, s.length) : 0), 0) > budget) {
      const info = body.map((s, i) => ({ i, s, legal: isLegal(s), score: specScore(s), dense: specDense(s) }));
      const rest = info.slice(1);
      const order = [info[0]]
        .concat(rest.filter((p) => !p.legal && p.score > 0).sort((a, b) => b.dense - a.dense || a.i - b.i))
        .concat(rest.filter((p) => !p.legal && p.score === 0))
        .concat(rest.filter((p) => p.legal).slice(0, 1));
      take = body.map(() => '');
      let room = budget;
      for (const p of order) {
        const n = Math.min(p.s.length, p.legal ? LEGAL_CLIP : PAGE_CLIP, room - cost(p.i, 0));
        if (n <= 0 || (n < 200 && n < p.s.length)) continue; // 剩的位置放不下有意義的一段：這頁不放
        take[p.i] = p.s.slice(0, n);
        room -= cost(p.i, n);
      }
    }
    return take.map((s, i) => (s ? mark(i) + s : '')).filter(Boolean).join('\n');
  }

  // 規則要看哪些字：頁數多的文件（簡報）只看第一頁和規格最密的兩頁，不然整份掃下去會把圖表裡的字、別的表格當成欄位
  function ruleText(pages) {
    const list = (Array.isArray(pages) ? pages : [pages]).map((s) => String(s || ''));
    if (list.length <= 3) return list.join('\n');
    const best = list.map((s, i) => ({ i, s, d: i ? specDense(s) : 0 })).filter((x) => x.i && specScore(x.s) >= 8).sort((a, b) => b.d - a.d).slice(0, 2).sort((a, b) => a.i - b.i);
    return [list[0]].concat(best.map((x) => x.s)).join('\n');
  }

  // AI 回的一檔商品 → 檢查過的 { type, 各欄位 }：只收字（物件、陣列不要），風險等級只認 RR1～RR5
  function cleanAi(ai) {
    const src = ai && typeof ai === 'object' ? ai : {};
    const out = { type: TYPES[src.type] ? src.type : '' };
    FK.forEach((k) => {
      const got = typeof src[k] === 'string' || typeof src[k] === 'number' ? String(src[k]) : '';
      out[k] = k === 'risks' ? many(got, 1200) : one(got, k === 'name' ? 60 : 200);
    });
    if (!/^RR[1-5]$/.test(out.risk)) out.risk = '';
    return out;
  }
  // 伺服器回的結果 → 幾檔商品（什麼都沒讀到的不算；同名的只留第一個；最多 6 檔）
  function aiProducts(d) {
    const rows = Array.isArray(d?.products) ? d.products : d?.product ? [d.product] : [];
    const out = [];
    rows.map(cleanAi).forEach((c) => {
      if (FK.some((k) => c[k]) && !out.some((x) => x.name && x.name === c.name)) out.push(c);
    });
    return out.slice(0, 6);
  }

  // AI 讀到的一檔商品，填進表單會是什麼樣子：AI 有讀完就以 AI 為準，AI 沒寫的欄位空著。
  // 規則抓的（base）在簡報類文件上常常是雜句，只當等 AI 時的預覽和 AI 不能用時的備案；AI 連名稱都沒寫時才留原本的名稱（檔名）
  function productTarget(base, ai) {
    const c = cleanAi(ai);
    const p = {};
    FK.forEach((k) => { p[k] = c[k]; });
    if (!p.name) p.name = base.p.name || '';
    return { type: c.type || base.type, p };
  }

  // 把上面算出來的樣子併進表單：等 AI 的這段時間使用者自己改過的欄位和類型不動。
  // raw 是表單現在的內容，snap 是送去給 AI 之前的樣子，ai 是伺服器回的一檔商品。回傳改了幾格。
  function mergeProduct(raw, snap, ai) {
    let n = 0;
    if (!ai || typeof ai !== 'object') return n;
    const c = cleanAi(ai);
    if (!FK.some((k) => c[k])) return n; // AI 什麼都沒讀到：表單原樣，不要把規則抓的清光
    const target = productTarget(snap, ai);
    if (raw.type === snap.type && target.type !== raw.type) applyType(raw, target.type);
    FK.forEach((k) => {
      if (raw.p[k] !== snap.p[k] || target.p[k] === raw.p[k]) return;
      raw.p[k] = target.p[k];
      n += 1;
    });
    return n;
  }

  // ---- 商品檔的文字 → 欄位（規則照生成器；抓不到就留空給人填）。這是不靠 AI 的那一段：馬上有結果，AI 不能用時也還有它
  function parseProduct(t, fname) {
    t = String(t || '');
    fname = String(fname || '');
    const g = (re) => { const m = t.match(re); return m ? m[1].trim() : ''; };
    const o = {};
    o.name = g(/(?:商品名稱|產品名稱|債券名稱|基金名稱)[：:]\s*([^\n\r]+)/);
    o.issuer = g(/(?:發行機構|發行人|發行商|發行銀行)[：:]\s*([^\n\r]+)/);
    const isin = t.match(/\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b/);
    o.code = isin ? isin[1] : '';
    // 幣別只認文件上明寫的。以前「內容出現美元就填美元」是用猜的：投資美元債的台幣計價基金會被填錯，填錯比空著更糟
    o.ccy = g(/(?:計價幣別|計價貨幣|幣別)[：:]\s*([^\n\r]+)/);
    const rr = t.match(/RR\s*([1-5])/i);
    o.risk = rr ? `RR${rr[1]}` : '';
    o.coupon = g(/(?:票息|配息|息率|利率)[^：:\n]{0,8}[：:]\s*([^\n\r]+)/);
    o.tenor = g(/(?:到期日?|年期|期限|閉鎖期)[^：:\n]{0,6}[：:]\s*([^\n\r]+)/);
    o.rating = g(/(?:債券信用評等|信用評等|評等)[：:]\s*([^\n\r]+)/);
    if (!o.rating) { const m = t.match(/(?:Fitch|Moody'?s|S&P)[^\n\r]{0,40}/i); if (m) { o.rating = m[0].trim(); } }
    o.call = g(/(?:提前贖回|贖回條款|可贖回|提前買回)[^：:\n]{0,6}[：:]\s*([^\n\r]+)/);
    o.extra = g(/(?:額外條件|其他條件|特殊條件)[：:]\s*([^\n\r]+)/);
    o.risks = g(/(?:必揭露風險|主要風險|風險提示)[：:]\s*([^\n\r]+)/);
    if (!o.coupon) { const cm = t.match(/(?:票面利率|票息|年化)[^\n\r]{0,6}?(\d+(?:\.\d+)?\s*%)/); if (cm) { o.coupon = cm[0].trim(); } }
    if (!o.tenor) { const dm = t.match(/(?:到期日?|Maturity)[^\n\r\d]{0,6}(20\d\d[/\-.年]\s?\d{1,2}[/\-.月]\s?\d{1,2}日?)/i); if (dm) { o.tenor = dm[1]; } }
    if (!o.extra) { const lm = t.match(/閉鎖期[^\n\r]{0,24}/); if (lm) { o.extra = lm[0].trim(); } }
    if (!o.call) { const km = t.match(/[^\n\r。]{0,20}(?:有權|得)[^\n\r。]{0,12}提前(?:贖回|買回)[^\n\r。]{0,30}/); if (km) { o.call = km[0].trim(); } }
    if (!o.risks) {
      // 像「信用風險」「匯率風險：……」「有本金損失風險。」這種一項一項的短句才算；句子中間剛好有「風險」兩個字的（「考量投資風險，本公司…」）、
      // 「風險等級 RR4」和「主要風險」這種標題都不算。前面的「一、」「1.」拿掉
      const rl = t.split(/\n/).map((x) => x.trim().replace(/^(?:[一二三四五六七八九十]+|\d+)[、.．)）]\s*/, ''))
        .filter((x) => /[一-鿿]{2,}風險(?:[：:。]|$)/.test(x) && x.length < 60 && !/等級|RR\s*[1-5]/i.test(x) && !/^(?:主要|相關|投資|商品)*風險(?:提示|揭露|說明|因素|預告書?)?[：:。]?$/.test(x)).slice(0, 6);
      if (rl.length) { o.risks = rl.join('\n'); }
    }
    // 表格式的文件沒有冒號（「發行機構　某某公司」）：上面沒抓到的，再用「一行開頭是欄位名稱、空白後面是內容」抓一次
    const NEXT = /[ \t　]+(?:證券簡稱|證券代[號碼]|基金類型|追蹤指[標數]|計價幣別|風險(?:收益|報酬)?等級|經理費|保管費|基金經理人|(?:基金)?保管銀行|收益分配|發行價格?|發行機構|連結標的|履約價|下限價|天期|到期日)(?=[ \t　*：:]|$)/;
    const row = (labels) => { const m = t.match(new RegExp(`(?:^|\\n)[ \\t　]*(?:${labels})[*＊]?[ \\t　]+([^\\n\\r]+)`, 'i')); return m ? m[1].split(NEXT)[0].trim() : ''; };
    const fuller = (k, labels) => { const x = row(labels); if (x && (!o[k] || x.includes(o[k]))) { o[k] = x; } }; // 整行比上面只抓到數字的那一小段完整
    if (!o.issuer) o.issuer = row('發行機構|發行人|發行銀行|基金公司|經理公司');
    if (!o.ccy) o.ccy = row('計價幣別|計價貨幣|幣別');
    const dist = row('收益分配|配息政策|配息方式|配息頻率|配息機制'); // 表格裡明寫的配息方式，比上面隨手抓到的「…配息:11月底」可靠
    if (dist) o.coupon = dist;
    fuller('coupon', '配息率|配息政策|配息方式|配息頻率|配息|票面利率|票息');
    fuller('tenor', '天期|到期日|年期|基金類型|基金種類');
    if (!o.rating) o.rating = row('(?:發行機構|債券)?信用評等');
    o.under = g(/(?:連結標的|標的資產|連結資產|連結股票)[：:]\s*([^\n\r]+)/);
    o.strike = g(/(?:履約價|執行價|Strike)[^：:\n]{0,10}[：:]\s*([^\n\r]+)/i);
    o.ki = g(/(?:下限價|敲入|Knock[- ]?in|KI)[^：:\n]{0,10}[：:]\s*([^\n\r]+)/i);
    const ko = g(/(?:提前出場|自動提前|敲出|Knock[- ]?out|Autocall|KO)[^：:\n]{0,10}[：:]\s*([^\n\r]+)/i);
    if (ko) { o.call = ko; }
    if (!o.under) o.under = row('連結標的|標的資產|連結資產|連結股票');
    if (!o.strike) o.strike = row('履約價(?:格)?|執行價(?:格)?');
    if (!o.ki) o.ki = row('下限價(?:格)?|敲入價(?:格)?');
    if (!ko) { const k2 = row('提前出場(?:條件)?|自動提前出場(?:條件)?'); if (k2) { o.call = k2; } }
    if (!o.call) o.call = row('買回費用|贖回費用|提前贖回(?:條件)?');
    const stem = fname.replace(/\.[^.]+$/, '');
    if (!o.code) { const fi = stem.match(/[A-Z]{2}[A-Z0-9]{9}[0-9]/); if (fi) o.code = fi[0]; }
    if (!o.risk) { const fr = stem.match(/RR\s*([1-5])/i); if (fr) o.risk = `RR${fr[1]}`; }
    const head = stem + t.slice(0, 400);
    o.type = /FCN|固定配息|Fixed Coupon|股權連結/i.test(head) ? 'fcn' : /基金/.test(stem) || /ETF|投信/i.test(head) ? 'fund' : /債|bond/i.test(head) ? 'bond' : /基金|fund/i.test(head) ? 'fund' : '';
    if (!o.name) o.name = nameFromFile(fname); // 文件裡沒有寫「商品名稱：」：先用檔名，至少不用自己打
    return o;
  }

  // ---- 劇本文件（畫面上看的和下載的 Word 用同一份內容；word = true 時把樣式直接寫在表格上，Word 才看得懂）

  function tbl(head, rows, { word = false, widths = null } = {}) {
    const th = word ? ' style="background:#1B4F7F;color:#fff;border:1px solid #1B4F7F;padding:8px;text-align:left"' : '';
    const col = widths ? `<colgroup>${widths.map((x) => `<col style="width:${x}%">`).join('')}</colgroup>` : '';
    let h = `<table${word ? ' style="border-collapse:collapse;width:100%;table-layout:fixed"' : ' class="wz-table"'}>${col}<thead><tr>${head.map((x) => `<th${th}>${x}</th>`).join('')}</tr></thead><tbody>`;
    rows.forEach((r, i) => {
      if (r.span) {
        h += `<tr><td colspan="${head.length}"${word ? ' style="background:#D9E9F7;font-weight:bold;color:#1B4F7F;border:1px solid #D0DCE5;padding:6px"' : ' class="wz-span"'}>${r.span}</td></tr>`;
        return;
      }
      const td = word ? ` style="background:${i % 2 ? '#F4F8FC' : '#FFFFFF'};border:1px solid #D0DCE5;padding:8px;vertical-align:top;word-break:break-word"` : '';
      h += `<tr>${r.map((c) => `<td${td}>${c}</td>`).join('')}</tr>`;
    });
    return `${h}</tbody></table>`;
  }
  const ul = (items) => `<ul>${items.map((x) => `<li>${x}</li>`).join('')}</ul>`;
  const lst = (s) => ul(s.split('、').map(esc));
  const red = (s, word) => (word ? `<span style="color:#C00000;font-weight:bold">${s}</span>` : `<span class="wz-red">${s}</span>`);

  function sec1(ctx, ol, word) {
    const { p, c, pr } = ctx;
    const L = (x) => (x ? esc(x) : '—');
    const prodRows = fieldsOf(ctx.type).filter(([k]) => k !== 'risks').map(([k, label]) => [label,
      k === 'name' ? `<b>${esc(p.name)}</b>（${TYPES[ctx.type]}）` : k === 'risk' ? red(L(p.risk), word) : L(p[k])]);
    const info = tbl(['項目', '內容'], prodRows.concat([
      ['必揭露風險', red(riskList(ctx).map(esc).join('<br>'), word)],
      ['客戶人設', `<b>${esc(c.name)}</b>｜${esc(pr.title)}｜${esc(c.age)}歲｜${esc(c.job)}｜${esc(c.asset)}`],
      ['核心抗拒', esc(pr.pain)], ['刁難度', red(DIFFS[ctx.d], word)], ['製作窗口', esc([ctx.w.unit, ctx.w.name].filter(Boolean).join('／') || '—')],
    ]), { word, widths: [24, 76] });
    const part = (t, a) => `<h4>【${t}】</h4>${ul(a.map(esc))}`;
    return info + part('商品核心規格與亮點重點', ol.prod) + part('客戶態樣背景與核心痛點', ol.cust) + part('本次對練核心攻防情境', ol.atk);
  }

  function sec2(ctx, rows, word) {
    const out = rows.map((r) => (r.m ? { span: esc(r.l) } : [
      r.w === 'A' ? '<b>永豐超業小豐</b>' : `客戶（${esc(ctx.c.name)}）`, esc(r.t).replace(/\n/g, '<br>'),
      esc(ROUND_TIPS[r.w === 'A' ? 'adv' : 'cust'][r.s] || ''),
    ]));
    return tbl(['發話角色', '對話內容', '對練要點提醒'], out, { word, widths: [16, 54, 30] });
  }

  function sec3(ctx, word) {
    const { pr, type } = ctx;
    const k1 = firstPain(ctx).k;
    const k2 = P2K[type].k;
    const R = [
      ['第 1 輪｜同理破冰', '被推銷的戒備，帶出口頭禪與過往創傷', '主動破冰、掌握市場話題、說明來意並徵詢時間、先同理', '一開口就推商品、說「歡迎光臨」、話術像唸稿'],
      ['第 2 輪｜需求探詢', '不想被問太多，但需要被理解', '資金期間、波動承受度、過往經驗（KYC）', '不問需求就推薦'],
      ['第 3 輪｜白話說明', '聽不懂專有名詞、怕被唬弄', '白話比喻、收益條件、期間、額外條件、主動完整揭露必揭露風險', '連續講英文縮寫、只講優點、遺漏風險'],
      [`第 4 輪｜痛點迎擊（一）：「${fill(ctx)(linesOf(ctx).p1c)}」`, '核心恐懼被觸動', k1[0], k1[1]],
      ['第 5 輪｜痛點迎擊（二）', '對條款是否吃虧／風險是否被淡化的疑慮', k2[0], k2[1]],
      ['第 6 輪｜促成下一步', '猶豫、想回去想', '重述非存款／不保證收益、期間與閉鎖、申購方式與交付文件；不催促並約定下一步', '催促成交、保證、建議借貸投資、未說明交易規則'],
    ];
    const t1 = tbl(['輪次', '客戶質疑與心理', '超業應答得分要點（必提關鍵字）', '地雷扣分陷阱'],
      R.map((r) => [`<b>${esc(r[0])}</b>`, esc(r[1]), lst(r[2]), red(lst(r[3]), word)]), { word, widths: [25, 25, 25, 25] });
    const O = objOf(ctx).map((o) => [esc(o[0]), esc(o[1]), lst(o[2]), red(lst(o[3]), word)]);
    const extra = ctx.ph.filter((x) => !objOf(ctx).some((o) => o[0].includes(x))).slice(0, 2)
      .map((x) => [`「${esc(x)}」`, '客戶口頭禪背後的情緒', ul(['先同理，再以客觀資訊與風險揭露回應']), red(ul(['情緒性回嘴、保證性語言']), word)]);
    return `${t1}<h4>核心異議攻防（客戶常見異議）</h4>${tbl(['客戶異議', '背後心理', '得分要點', '地雷陷阱'], O.concat(extra), { word, widths: [25, 25, 25, 25] })}`;
  }

  // 依範本對話自評（規則照生成器：必揭露風險有沒有講到、有沒有紅線用語、有沒有說明交易規則）
  function selfScore(ctx, rows) {
    const adv = rows.filter((r) => r.w === 'A').map((r) => r.t).join('\n');
    const rs = riskList(ctx);
    const cov = rs.length ? rs.filter((x) => adv.includes(x)).length / rs.length : 0;
    const safe = adv.replace(/沒有保證收益|不能保證|無法保證|不會跟您保證|不會保證任何結果|不會保證|不能跟您保證|不保證|不是保證|非保證|不是保本|不是存款|不保本|不是存款保險|不是任何保證/g, '');
    const bad = safe.match(/保證|穩賺|不會賠|保本|比定存|借錢|貸款/g) || [];
    const rule = /閉鎖|期間|到期/.test(adv) && /風險預告書|說明書/.test(adv);
    const sc = { struct: 5, risk: Math.round(7 * cov), adv: 6, fit: 7, cmp: ctx.pr.id === 'eng' ? 7 : 6, case: 6, open: 7, need: 7, obj: 7, close: 6, bal: 7, emp: 5, lang: Math.max(0, 10 - 4 * bad.length), rule: rule ? 10 : 6 };
    const total = ITEMS.reduce((a, it) => a + sc[it[3]], 0);
    const top = STRONG.filter((k) => sc[k] >= ITEMS.find((x) => x[3] === k)[2]).slice(0, 3);
    return { sc, total, top, cov, bad: bad.length };
  }

  function sec4(ctx, rows, word) {
    const { sc, total, top, cov, bad } = selfScore(ctx, rows);
    const star = (s) => (word ? `<span style="color:#0a7a45;font-weight:bold">★ ${s}</span>` : `<span class="wz-star">★ ${s}</span>`);
    const table = tbl(['面向', '評分項目', '本腳本得分', '亮點落點／說明'], ITEMS.map((it) => [
      it[0] ? `<b>${it[0]}</b>` : '', it[1], `<b>${sc[it[3]]} / ${it[2]}</b>`, sc[it[3]] >= it[2] ? star(it[4]) : it[5],
    ]), { word, widths: [16, 22, 14, 48] });
    return `<p><b>本腳本自評總分：${red(`${total} / 100`, word)}</b>（產品面 40％｜銷售面 40％｜合規面 20％）</p>`
      + `<p><b>三大必學亮點</b></p><ol>${top.map((k) => `<li>${esc(STRONG_TXT[k])}</li>`).join('')}</ol>${table}`
      + `<p>※ 以上是依範本對話用固定規則算出來的自評（風險揭露覆蓋 ${Math.round(cov * 100)}%；紅線用語偵測 ${bad} 項），為教材標示用途，正式評分以業務擂台評分模型為準。</p>`;
  }

  const WORD_CSS = 'body{font-family:"微軟正黑體","Microsoft JhengHei",Arial;font-size:11pt;color:#20242B}table{border-collapse:collapse;width:100%}th,td{font-family:"微軟正黑體"}td{vertical-align:top}h4{color:#1B4F7F;margin:14px 0 6px}';
  const wordPage = (title, body) => `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40"><head><meta charset="utf-8"><title>${esc(title)}</title><style>${WORD_CSS}</style></head><body>${body}</body></html>`;
  const h2 = (t) => `<h2 style="color:#1B4F7F;border-bottom:2px solid #C00000">${esc(t)}</h2>`;

  // 三步驟做的情境：完整四個章節
  function docHtml(ctx, ol, rows, date) {
    const body = `<h1 style="color:#1B4F7F">AI 業務擂台｜Role Play 劇本與超業範本</h1><p>製作單位：${esc(ctx.w.unit || '—')}　窗口：${esc(ctx.w.name || '—')}　日期：${esc(date || '')}</p>`
      + h2(SECTIONS[0][1]) + sec1(ctx, ol, true) + h2(SECTIONS[1][1]) + sec2(ctx, rows, true) + h2(SECTIONS[2][1]) + sec3(ctx, true) + h2(SECTIONS[3][1]) + sec4(ctx, rows, true);
    return wordPage('Role Play', body);
  }

  // 沒有劇本的情境（內建的、用進階編輯寫的）：只有公開的內容，沒有超業範本
  function plainDocHtml(sc, date) {
    const cu = sc.customer || {};
    const L = (x) => (x || x === 0 ? esc(x) : '—');
    const info = tbl(['項目', '內容'], [
      ['情境', `<b>${L(sc.title)}</b>`], ['商品', L(sc.product)], ['客戶', `<b>${L(cu.name)}</b>｜${L(cu.age)} 歲｜${L(cu.job)}`], ['客戶背景', L(cu.summary)],
      ['難度', L(sc.difficulty)], ['通話方向', sc.call_direction === 'outgoing' ? '由業務撥出' : '客戶來電'], ['建議時間', `${L(sc.duration_min)} 分鐘`],
      ['一開始的信任度', L(sc.initial_trust)], ['客戶第一句話', L(sc.opening_line)], ['本次任務', L(sc.learner_task)],
    ], { word: true, widths: [24, 76] });
    const checks = tbl(['檢核項目', '提示'], (sc.checklist || []).map((c) => [esc(c.label), L(c.hint)]), { word: true, widths: [34, 66] });
    const demos = (sc.demo_lines || []).length ? ul(sc.demo_lines.map(esc)) : '<p>（這個情境沒有示範句）</p>';
    const rubric = tbl(['面向', '評分項目', '滿分'], ITEMS.map((it) => [it[0] ? `<b>${it[0]}</b>` : '', it[1], String(it[2])]), { word: true, widths: [24, 56, 20] });
    const body = `<h1 style="color:#1B4F7F">AI 業務擂台｜對練情境</h1><p>日期：${esc(date || '')}</p>`
      + h2('一、對練背景') + info + h2('二、檢核項目') + checks + h2('三、示範句') + demos + h2('四、100 分制評分指標') + rubric
      + '<p>※ 這個情境不是用三步驟建立的，沒有超業範本對話。</p>';
    return wordPage('對練情境', body);
  }

  // 下載的 Word 檔名：Role play腳本_[標的]_[客戶態樣]_[日期].doc。底線只當分隔，所以名稱裡的底線和不能當檔名的字換成空白；日期用使用者電腦當天的 20261125
  const filePart = (s, n) => String(s || '').replace(/[\\/:*?"<>|_\s]+/g, ' ').trim().slice(0, n).trim();
  const dayStamp = (d) => `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`;
  const wordName = (product, persona, when) => `Role play腳本_${filePart(product, 60) || '商品'}_${filePart(persona, 20) || '客戶'}_${dayStamp(when || new Date())}.doc`;

  const FILE_KIND = 'ai-arena-scenario';
  const exportData =(pb, title, when) => ({ kind: FILE_KIND, v: 1, exported_at: when || '', title: String(title || ''), playbook: pb });
  function readExport(data) {
    if (!data || typeof data !== 'object' || data.kind !== FILE_KIND) throw new Error('這不是 AI 業務擂台的情境檔');
    if (data.v !== 1) throw new Error('這個情境檔的版本比較新，這一版讀不了');
    if (!data.playbook || typeof data.playbook !== 'object') throw new Error('情境檔裡沒有內容');
    if (!String(data.playbook.p?.name || '').trim()) throw new Error('情境檔裡沒有商品名稱');
    return fromPlaybook(data.playbook);
  }

  const logic = { TYPES, UNITS, FK, FIELDS, fieldsOf, fitsType, personaFor, PH, PHRASES, DIFF_OPTS, PERSONAS, SECTIONS, normalize, riskList, buildOutline, cleanOutline, buildChat, cleanRows, toScenario, fromPlaybook, recipeKey, parseProduct, fixCjk, nameFromFile, applyType, pickText, ruleText, cleanAi, aiProducts, productTarget, mergeProduct, sec1, sec2, sec3, sec4, selfScore, docHtml, plainDocHtml, wordName, exportData, readExport };

  // ------------------------------------------------------------------ 畫面（以下只在瀏覽器裡跑）

  let H = null; // app.js 給的工具：api、toast、go、show、setNav、reloadScenarios、startCall、avatarSVG、openEditor、scenario
  const WINDOW_KEY = 'aicoach.arenaWindow';
  // up、ctrl、note、cands 是上傳商品檔用的：第幾次上傳、還在等的 AI 請求、上傳框下面那行字、文件裡有好幾檔商品時的清單
  // tpl 不是 null 的時候，這一頁是在編輯「商品範本」（不是三步驟）：上半部沿用第一步的商品欄位，下半部是給教練當標準的內容
  const W = { step: 1, raw: null, phrases: [], editId: '', saved: null, base: null, dirty: false, busy: false, gen: 0, up: 0, ctrl: null, note: null, cands: null, tpl: null };
  const $id = (id) => document.getElementById(id);
  const today = () => new Date().toLocaleDateString('zh-TW');
  const stamp = () => new Date().toISOString().slice(0, 10);

  function blankRaw() {
    let w = {};
    try { w = JSON.parse(localStorage.getItem(WINDOW_KEY) || '{}') || {}; } catch { /* 讀不到就空白 */ }
    const p = {};
    FK.forEach((k) => { p[k] = ''; });
    return { type: Object.keys(TYPES)[0], p, pid: '', av: '', c: { name: '', age: '', job: '', asset: '' }, ph: [], d: 2, w: { unit: w.unit || '', name: w.name || '' } };
  }

  // ---- 檔案解析：在瀏覽器裡把檔案變成文字（.txt 直接讀；.docx 解壓縮後拿文字；.pdf 用 pdf.js）。
  // 檔案本身不會上傳；抽出來的文字會送給伺服器上的 AI 判讀欄位（/api/editor/product）
  const FILE_MAX = 20 * 1024 * 1024;
  const DOC_TEXT_MAX = 20000; // 送給 AI 的文字上限，和伺服器的 PRODUCT_TEXT_MAX 一樣
  const AI_TEXT_MIN = 20; // 讀到的字比這還少：當作讀不到，不問 AI
  const AI_WAIT_MS = 90000;
  const PDF_PAGES = 80; // 最多讀這麼多頁。整份都讀，再由 pickText 挑要送給 AI 的頁：規格常常在簡報的後半段（例如第 24 頁的基金小檔案）
  const NOTE0 = '還沒上傳；每個欄位都可以手動填、手動改。';
  // pdf.js（Mozilla 的 PDF 元件）：第一次上傳 PDF 才下載。固定版本加上完整性檢查，CDN 上的檔案被換掉就不會執行（跟 liveavatar.js 載 LiveKit 一樣）
  const PDFJS = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38';
  const PDFJS_LIB = ['/legacy/build/pdf.min.mjs', 'sha384-z/XbAtvhBXJoK+yvYimnFuTCF6/2wTCDSIFD09/HtIfwFtvDKOMdAe5Z3wOChkmW'];
  const PDFJS_WORKER = ['/legacy/build/pdf.worker.min.mjs', 'sha384-vbNBYP03jHFPlgtK4qSXtazEmGfmpTWlSsfq9OiU20MroW/Q9TbaEXiM7PpeWg+6'];
  const inflate = (bytes, fmt) => new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream(fmt))).arrayBuffer().then((b) => new Uint8Array(b));
  const decodeEnt = (s) => { const t = document.createElement('textarea'); t.innerHTML = s; return t.value; };
  async function readDocx(file) {
    const buf = await file.arrayBuffer();
    const dv = new DataView(buf);
    const u8 = new Uint8Array(buf);
    let e = -1;
    for (let i = u8.length - 22; i >= 0; i -= 1) { if (dv.getUint32(i, true) === 0x06054b50) { e = i; break; } }
    if (e < 0) return '';
    const cnt = dv.getUint16(e + 10, true);
    let p = dv.getUint32(e + 16, true);
    for (let k = 0; k < cnt; k += 1) {
      if (dv.getUint32(p, true) !== 0x02014b50) break;
      const method = dv.getUint16(p + 10, true);
      const cs = dv.getUint32(p + 20, true);
      const nl = dv.getUint16(p + 28, true);
      const xl = dv.getUint16(p + 30, true);
      const cl = dv.getUint16(p + 32, true);
      const off = dv.getUint32(p + 42, true);
      if (new TextDecoder().decode(u8.subarray(p + 46, p + 46 + nl)) === 'word/document.xml') {
        const st = off + 30 + dv.getUint16(off + 26, true) + dv.getUint16(off + 28, true);
        let data = u8.subarray(st, st + cs);
        if (method === 8) data = await inflate(data, 'deflate-raw');
        return decodeEnt(new TextDecoder().decode(data).replace(/<\/w:p>/g, '\n').replace(/<w:tab\/>/g, ' ').replace(/<[^>]+>/g, ''));
      }
      p += 46 + nl + xl + cl;
    }
    return '';
  }
  let pdfjs = null;
  function loadPdfjs() {
    if (!pdfjs) {
      // 先用 fetch 下載並核對完整性，再從記憶體載入（import() 自己沒辦法核對）
      const grab = async ([path, integrity]) => {
        const r = await fetch(PDFJS + path, { integrity, credentials: 'omit' });
        if (!r.ok) throw new Error(`pdf.js ${r.status}`);
        return URL.createObjectURL(new Blob([await r.arrayBuffer()], { type: 'text/javascript' }));
      };
      pdfjs = Promise.all([grab(PDFJS_LIB), grab(PDFJS_WORKER)]).then(async ([lib, worker]) => {
        const m = await import(lib);
        m.GlobalWorkerOptions.workerSrc = worker;
        return m;
      });
      pdfjs.catch(() => { pdfjs = null; }); // 下載不了：下次上傳再試
    }
    return pdfjs;
  }
  // 回傳 { pages, why }：pages 是每一頁的文字；why 是讀不到字的原因（給畫面上那行字用）
  async function readPdf(file) {
    const data = new Uint8Array(await file.arrayBuffer());
    let lib;
    try { lib = await loadPdfjs(); } catch {
      // 備案讀出來的常常是亂碼：要大部分是看得懂的字（中文至少 20 個，或英文單字至少 15 個），不然當作沒讀到，不拿去問 AI
      const rough = await readPdfRough(data).catch(() => '');
      const count = (re) => (rough.match(re) || []).length;
      const ok = count(/[一-鿿\w\s，。、：；（）%.,:;()/-]/g) >= rough.length * 0.85 && (count(/[一-鿿]/g) >= 20 || count(/[A-Za-z]{3,}/g) >= 15);
      return { pages: [ok ? rough : ''], why: '讀不到文字（讀 PDF 的元件下載不了，請檢查網路後再傳一次）' };
    }
    const task = lib.getDocument({ data, cMapUrl: `${PDFJS}/cmaps/`, cMapPacked: true, standardFontDataUrl: `${PDFJS}/standard_fonts/`, isEvalSupported: false, disableFontFace: true, verbosity: 0 });
    try {
      const doc = await task.promise;
      const pages = [];
      for (let i = 1; i <= Math.min(doc.numPages, PDF_PAGES); i += 1) {
        const tc = await (await doc.getPage(i)).getTextContent();
        pages.push(tc.items.map((it) => (it.str || '') + (it.hasEOL ? '\n' : '')).join(''));
      }
      return { pages, why: '是掃描的圖片，裡面沒有可以讀的文字' };
    } catch (e) {
      return { pages: [], why: e?.name === 'PasswordException' ? '有密碼保護，打不開' : '打不開（檔案可能壞了）' };
    } finally {
      Promise.resolve(task.destroy()).catch(() => {});
    }
  }
  // pdf.js 下載不了時的備案：只抓得到沒壓縮、沒編碼的簡單文字，中文 PDF 多半抓不到
  async function readPdfRough(u8) {
    const s = new TextDecoder('latin1').decode(u8);
    const out = [];
    let pos = 0;
    for (;;) {
      const a = s.indexOf('stream', pos);
      if (a < 0) break;
      let st = a + 6;
      if (s[st] === '\r') st += 1;
      if (s[st] === '\n') st += 1;
      const b = s.indexOf('endstream', st);
      if (b < 0) break;
      let txt = '';
      try { txt = new TextDecoder('latin1').decode(await inflate(u8.subarray(st, b), 'deflate')); } catch { txt = s.substring(st, b); }
      txt.replace(/BT([\s\S]*?)ET/g, (m, blk) => {
        blk.replace(/\(((?:\\.|[^\\)])*)\)/g, (x, t) => { out.push(t.replace(/\\(.)/g, '$1')); return x; });
        blk.replace(/<([0-9A-Fa-f]{4,})>/g, (x, hex) => { let r = ''; for (let i = 0; i + 3 < hex.length; i += 4) r += String.fromCharCode(parseInt(hex.substr(i, 4), 16)); out.push(r); return x; });
        out.push('\n');
        return m;
      });
      pos = b + 9;
    }
    return out.join('');
  }

  // 上傳框下面那行字。busy 是「還在跑」；idle 是中途被放掉（離開這一步）時要改顯示的字
  function setNote(text, busy = false, idle = '') {
    W.note = { text, busy, idle };
    const el = $id('wzFileInfo');
    if (el) { el.textContent = text; el.classList.toggle('busy', busy); }
  }
  // 放掉還在跑的上傳：又傳了一個、離開第一步、關掉這一頁。AI 晚回來的結果就不用了
  function dropUpload() {
    W.up += 1;
    W.ctrl?.abort();
    W.ctrl = null;
    if (W.note?.busy) W.note = { text: W.note.idle || NOTE0, busy: false, idle: '' };
  }

  // 文件裡有好幾檔商品時，下拉選單換一檔：欄位整個換成那一檔的內容（選單出現之後自己改過的，先問一聲）
  function pickCandidate(i) {
    const c = W.cands;
    const sel = $id('wzPick');
    if (!c || !c.list[i] || i === c.pick) return;
    readForm();
    const edited = FK.some((k) => W.raw.p[k] !== c.shown[k]);
    const ask = W.tpl && tplHasStd() ? '換一檔會把商品欄位換成那一檔的內容，下面「給教練當標準的內容」也會清空（那是照現在這一檔寫的）。要換嗎？'
      : edited ? '換一檔會把下面改過的欄位換成那一檔的內容，要換嗎？' : '';
    if (ask && !window.confirm(ask)) { if (sel) sel.value = String(c.pick); return; }
    const target = productTarget(c.base, c.list[i]);
    applyType(W.raw, target.type);
    W.raw.p = { ...target.p };
    c.pick = i;
    c.shown = { ...target.p };
    W.dirty = true;
    setNote(`這份文件有 ${c.list.length} 檔商品，現在填的是「${target.p.name}」，請核對後修改。`);
    if (W.tpl && tplHasStd()) { // 範本下半部是照上一檔寫的：換了一檔就不能留著
      Object.assign(W.tpl, { points: '', qa: [], donts: '', note: '已經換成另一檔商品，下面三欄清空了，請按「AI 幫我寫」重寫。' });
      W.tpl.gen += 1;
      W.tpl.busy = false;
    }
    render();
  }

  // 上傳商品檔：1. 讀出文字 → 2. 檔名和規則先帶入（馬上看得到）→ 3. AI 讀內容，讀到的補上去（不只一檔就給下拉選單）
  async function takeFile(f) {
    const got = await readFile(f);
    // 編輯商品範本時：AI 讀完商品資料，下半部（白話重點、常見質疑、不能說的話）還是空的，就接著請 AI 寫草稿。
    // 文件裡有好幾檔商品時先不寫：等使用者用選單選好是哪一檔，再自己按「AI 幫我寫」
    if (got && W.tpl && got.up === W.up && !got.multi && !tplHasStd()) tplAi(true);
  }
  async function readFile(f) {
    if (!f || !$id('wzFileInfo')) return null;
    const ext = f.name.split('.').pop().toLowerCase();
    if (!['txt', 'pdf', 'docx'].includes(ext)) { setNote(`不支援的格式：${f.name}（請用 .docx、.txt、.pdf）`); return null; }
    if (f.size > FILE_MAX) { setNote(`「${f.name}」太大了（最多 20 MB）。`); return null; }
    dropUpload();
    const my = W.up;
    W.cands = null;
    $id('wzPick')?.closest('label')?.remove(); // 上一份文件的下拉選單先收掉
    setNote(`正在讀「${f.name}」…`, true);
    let pages = [];
    let why = '裡面讀得到的文字太少';
    try {
      if (ext === 'txt') pages = [await f.text()];
      else if (ext === 'docx') pages = [await readDocx(f)];
      else ({ pages, why } = await readPdf(f));
    } catch { pages = []; }
    if (my !== W.up) return null;
    pages = pages.map(fixCjk);
    const text = pickText(pages, DOC_TEXT_MAX); // 送給 AI 的那一段
    const o = parseProduct(ruleText(pages), f.name); // 規則：馬上有結果，AI 讀完之前先看到的
    readForm();
    FK.forEach((k) => { W.raw.p[k] = o[k] ? (k === 'risks' ? many(o[k], 1200) : one(o[k], k === 'name' ? 60 : 200)) : ''; });
    if (o.type) applyType(W.raw, o.type);
    W.dirty = true;
    if (W.tpl) Object.assign(W.tpl, { text, source: one(f.name, 120) }); // 範本：文件的字留著，等一下請 AI 寫草稿時一起給它看（只留在這一頁，不存）
    const filled = () => fieldsOf(W.raw.type).filter(([k]) => W.raw.p[k]).length;
    if (text.replace(/\s+/g, '').length < AI_TEXT_MIN) {
      setNote(`「${f.name}」${why}，先用檔名當商品名稱，其他欄位請手動填。`);
      render();
      return null;
    }
    const done = `已從「${f.name}」帶入 ${filled()} 個欄位，請核對後修改。`;
    setNote(`已從「${f.name}」帶入 ${filled()} 個欄位，AI 正在讀內容，讀完會自動補上（大約 10～30 秒）…`, true, done);
    render();
    const snap = { type: W.raw.type, p: { ...W.raw.p } };
    const ctrl = new AbortController();
    W.ctrl = ctrl;
    const timer = setTimeout(() => ctrl.abort(), AI_WAIT_MS);
    let d = null;
    let err = '';
    try { d = await H.api('/api/editor/product', { text, filename: f.name }, ctrl.signal); } catch (e) { err = e.name === 'AbortError' ? 'AI 讀太久' : (e.message || 'AI 沒有回應'); }
    clearTimeout(timer);
    if (my !== W.up) return null; // 又傳了一個，或已經離開第一步：這份結果不用了
    W.ctrl = null;
    if (!d) { setNote(`已從「${f.name}」帶入 ${filled()} 個欄位（${err}，這次沒有用到 AI），請核對後手動補上。`); return null; }
    readForm(); // 等的時候使用者可能改了幾格：先收回來，改過的不蓋
    const list = aiProducts(d);
    if (!list.length) { setNote(`${done.slice(0, -1)}（AI 也讀過了，沒有讀到要補的）。`); return { up: my, multi: false }; }
    const multi = list.length > 1;
    const before = JSON.stringify([W.raw.type, W.raw.p]);
    mergeProduct(W.raw, snap, list[0]);
    if (multi) W.cands = { list, pick: 0, base: snap, shown: { ...W.raw.p } };
    setNote(multi ? `AI 已讀完「${f.name}」：裡面有 ${list.length} 檔商品，先帶入第一檔，可以用下面的選單換。請核對後修改。`
      : before !== JSON.stringify([W.raw.type, W.raw.p]) ? `AI 已讀完「${f.name}」，帶入 ${filled()} 個欄位，請核對後修改。`
        : `${done.slice(0, -1)}（AI 也讀過了，沒有要補的）。`);
    render();
    return { up: my, multi };
  }

  // ---- 把畫面上的值收回 W.raw（編輯範本時，下半部收回 W.tpl）
  function readForm() {
    const box = $id('wizardBody');
    if (!box || !W.raw) return;
    box.querySelectorAll('[data-p]').forEach((e) => { W.raw.p[e.dataset.p] = e.value; });
    box.querySelectorAll('[data-c]').forEach((e) => { W.raw.c[e.dataset.c] = e.value; });
    box.querySelectorAll('[data-w]').forEach((e) => { W.raw.w[e.dataset.w] = e.value; });
    if (!W.tpl) return;
    box.querySelectorAll('[data-t]').forEach((e) => { W.tpl[e.dataset.t] = e.value; });
    const rows = [...box.querySelectorAll('.tpl-qa-row')].map((r) => ({ q: r.querySelector('[data-qa="q"]').value, a: r.querySelector('[data-qa="a"]').value }));
    if (rows.length) W.tpl.qa = rows; // 空的那幾組先留著（存的時候才拿掉），畫面上第幾組才對得上
  }
  const readOutline = () => {
    const pick = (k) => [...document.querySelectorAll(`#wizardBody [data-ol="${k}"] li`)].map((li) => li.textContent.trim()).filter(Boolean);
    return { prod: pick('prod'), cust: pick('cust'), atk: pick('atk') };
  };
  const readRows = () => [...document.querySelectorAll('#wizardBody .wz-chat > *')].map((n) => (n.dataset.m
    ? { m: 1, s: n.dataset.s, l: n.textContent.trim() }
    : { s: n.dataset.s, w: n.dataset.w, t: n.querySelector('.wz-bub').innerText.trim() }));

  // ---- 三步驟的畫面
  const stepsBar = () => `<ol class="wz-steps">${[['商品', '可上傳 DM 帶入'], ['客戶人設', '口頭禪與刁難度'], ['確認大綱', '建立情境']].map(([t, s], i) =>
    `<li class="${W.step === i + 1 ? 'on' : W.step > i + 1 ? 'done' : ''}"><button type="button" data-act="step" data-n="${i + 1}"><i>${i + 1}</i><span><b>${t}</b><small>${s}</small></span></button></li>`).join('')}</ol>`;
  const field = (attr, key, label, value, { wide = false, max = 200, ph = '', req = false } = {}) =>
    `<label class="field${wide ? ' wide' : ''}"><span>${label}${req ? ' <em class="wz-req">*</em>' : ''}</span><input type="text" ${attr}="${key}" maxlength="${max}" value="${esc(value)}" placeholder="${esc(ph)}" autocomplete="off" spellcheck="false"></label>`;

  const step1 = () => `${productForm()}
      <p class="wz-msg" id="wzMsg" role="alert"></p>
      <div class="form-foot">
        ${W.editId ? '' : '<button class="pill ghost" type="button" data-act="blank">改用空白表單（進階）</button>'}
        <button class="pill primary" type="button" data-act="next">下一步：客戶人設</button>
      </div>`;

  // 商品類型、上傳商品檔、商品欄位：三步驟的第一步和「商品範本」共用
  function productForm() {
    const r = W.raw;
    const ph = PH[r.type];
    return `<div class="field"><span>商品類型</span><div class="seg" role="group" aria-label="商品類型">${Object.entries(TYPES).map(([k, t]) =>
      `<button type="button" data-act="type" data-v="${k}" class="${r.type === k ? 'on' : ''}">${t}</button>`).join('')}</div></div>
      <label class="wz-upload" id="wzDrop"><b>把商品 DM 或規格檔拖到這裡，或點一下選檔</b><span>.docx、.txt、.pdf；檔名會帶入商品名稱，AI 會讀內容填好下面的欄位</span>
        <input type="file" id="wzFile" accept=".txt,.pdf,.docx" hidden><span class="muted${W.note?.busy ? ' busy' : ''}" id="wzFileInfo" aria-live="polite">${esc(W.note?.text || NOTE0)}</span></label>
      ${W.cands ? `<label class="field wz-pick"><span>這份文件有 ${W.cands.list.length} 檔商品，要用哪一檔？</span><select id="wzPick">${W.cands.list.map((c, i) =>
        `<option value="${i}"${i === W.cands.pick ? ' selected' : ''}>${esc(c.name || `第 ${i + 1} 檔`)}${TYPES[c.type] ? `（${TYPES[c.type]}）` : ''}</option>`).join('')}</select></label>` : ''}
      <div class="form-grid">${fieldsOf(r.type).map(([k, label]) => {
        if (k === 'risk') return `<label class="field"><span>${label}</span><select data-p="risk"><option value="">請選擇</option>${[1, 2, 3, 4, 5].map((n) => `<option${r.p.risk === `RR${n}` ? ' selected' : ''}>RR${n}</option>`).join('')}</select></label>`;
        if (k === 'risks') {
          return `<label class="field wide"><span>${label}</span><textarea data-p="risks" rows="4" maxlength="1200" placeholder="${esc(ph.risks || '')}">${esc(r.p.risks)}</textarea>
          <small class="muted">留空時，會套用這類商品通用的必揭露風險。</small></label>`;
        }
        return field('data-p', k, label, r.p[k], { wide: k === 'name' || k === 'extra' || k === 'under', max: k === 'name' ? 60 : 200, ph: ph[k] || '', req: k === 'name' });
      }).join('')}</div>`;
  }

  // ---- 商品範本：一檔商品一份。上半部是商品資料，下半部是給教練當標準的內容（白話重點、常見質疑和建議回應、不能說的話）和審核狀態
  const REVIEWER_KEY = 'aicoach.reviewer';
  const TPL_QA_MAX = 10;
  const TPL_NOTE0 = '上傳商品檔之後 AI 會接著寫這三欄；也可以自己填，或按右邊的按鈕。';
  const tplLines = (s) => String(s || '').split('\n').map((x) => one(x, 200)).filter(Boolean);
  const tplHasStd = () => !!W.tpl && !!(W.tpl.points.trim() || W.tpl.donts.trim() || W.tpl.qa.some((x) => x.q.trim() || x.a.trim()));
  const tplDay = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('zh-TW'); };
  const infoTip = (id, label, text) => `<button class="info-btn" type="button" aria-expanded="false" aria-controls="${id}" aria-label="說明：${esc(label)}">${H.icon('info')}</button>`
    + `<p class="info-pop" id="${id}" hidden>${text}</p>`;

  // 要存的（或要給 AI 看的）範本內容。必揭露風險沒填就帶入這類商品通用的：存下來的範本要看得到完整的風險清單，審核的人才有東西可以審
  function tplBody() {
    const r = W.raw;
    const t = W.tpl;
    const p = {};
    fieldsOf(r.type).forEach(([k]) => { p[k] = k === 'risks' ? many(r.p.risks, 1200) : one(r.p[k], k === 'name' ? 60 : 200); });
    if (!p.risks.trim()) p.risks = RISK_DEF[r.type].join('\n');
    return {
      type: r.type, p, points: tplLines(t.points), donts: tplLines(t.donts), source: t.source || '',
      qa: t.qa.map((x) => ({ q: one(x.q, 80), a: one(x.a, 300) })).filter((x) => x.q || x.a),
    };
  }

  function tplPage() {
    const t = W.tpl;
    const ok = t.review?.status === 'approved';
    const qa = t.qa.length ? t.qa : [{ q: '', a: '' }];
    return `${productForm()}
      <h2 class="tpl-h2">給教練當標準的內容${infoTip('tplStdInfo', '給教練當標準的內容',
    '對練時，AI 教練會拿這三欄當標準，判斷業務講得對不對；AI 寫情境時也會參考。AI 先寫草稿，公司審核的人再修改。')}</h2>
      <div class="tpl-ai"><span class="muted${t.busy ? ' busy' : ''}" id="tplAiNote" aria-live="polite">${esc(t.note || TPL_NOTE0)}</span>
        <button class="pill sm" type="button" data-act="tpl-ai"${t.busy ? ' disabled' : ''}>${tplHasStd() ? '請 AI 重寫' : 'AI 幫我寫'}</button></div>
      <label class="field wide"><span>白話重點</span>
        <textarea data-t="points" rows="4" maxlength="800" placeholder="一行一句，業務可以直接對客戶說的話。例：這是把錢交給投信買一籃子公司債，每個月配息一次。">${esc(t.points)}</textarea></label>
      <div class="field wide"><span>客戶常見的質疑和建議回應</span>
        <div class="tpl-qa">${qa.map((x, i) => `<div class="tpl-qa-row" data-i="${i}">
          <input type="text" data-qa="q" maxlength="80" value="${esc(x.q)}" placeholder="客戶會怎麼問" aria-label="第 ${i + 1} 組：客戶的質疑" autocomplete="off">
          <textarea data-qa="a" rows="2" maxlength="300" placeholder="建議業務怎麼回" aria-label="第 ${i + 1} 組：建議回應">${esc(x.a)}</textarea>
          <button class="pill sm ghost" type="button" data-act="tpl-qa-del">刪除</button></div>`).join('')}</div>
        ${qa.length < TPL_QA_MAX ? '<div><button class="pill sm ghost" type="button" data-act="tpl-qa-add">＋ 加一組</button></div>' : ''}</div>
      <label class="field wide"><span>這檔商品不能說的話</span>
        <textarea data-t="donts" rows="4" maxlength="900" placeholder="一行一條。例：不能把來自收益平準金的配息說成獲利。">${esc(t.donts)}</textarea></label>
      <h2 class="tpl-h2">審核${infoTip('tplReviewInfo', '審核',
    '未審核的範本一樣可以拿來對練，畫面上會標「未審核」。審核通過之後，只要內容有改，就會回到未審核。')}</h2>
      <div class="tpl-review">
        <span class="tag ${ok ? 'green' : 'amber'}">${ok ? '已審核' : '未審核'}</span>
        <span class="muted">${ok ? `${esc(t.review.by)}　${esc(tplDay(t.review.at))}` : '還沒有人審核'}</span>
        <label class="field"><span>審核人</span><input type="text" data-t="by" maxlength="20" value="${esc(t.by)}" placeholder="要按「儲存並審核通過」才需要填" autocomplete="off"></label>
      </div>
      <p class="wz-msg" id="wzMsg" role="alert"></p>
      <div class="form-foot">
        <button class="pill ghost" type="button" data-act="list">取消</button>
        <button class="pill" type="button" data-act="tpl-save">儲存</button>
        <button class="pill primary" type="button" data-act="tpl-approve">儲存並審核通過</button>
      </div>`;
  }

  // 請 AI 寫範本下半部的草稿。auto：剛上傳完商品檔自動接著寫（這時三欄都是空的，不用問）
  async function tplAi(auto = false) {
    const t = W.tpl;
    if (!t || t.busy) return;
    readForm();
    if (!W.raw.p.name.trim()) { msg('請先填商品名稱，或上傳商品檔。'); return; }
    if (!auto && tplHasStd() && !window.confirm('AI 會重寫「白話重點、常見質疑、不能說的話」，現在的內容會被換掉。要繼續嗎？')) return;
    t.gen += 1;
    const my = t.gen;
    Object.assign(t, { busy: true, note: 'AI 正在寫白話重點、常見質疑和不能說的話（大約 10～30 秒）…' });
    render();
    let d = null;
    let err = '';
    try { d = await H.api('/api/template/draft', { template: tplBody(), text: t.text || '' }); } catch (e) { err = e.message || 'AI 沒有回應'; }
    if (W.tpl !== t || my !== t.gen) return; // 已經離開這一頁，或換了一檔商品：這份草稿不用了
    readForm(); // 等的時候使用者可能改了上面的欄位：先收回來
    t.busy = false;
    if (!d?.draft) {
      t.note = `AI 這次沒寫出來（${err || '內容不完整'}），可以再按一次，或自己填。`;
    } else {
      Object.assign(t, { points: d.draft.points.join('\n'), qa: d.draft.qa.map((x) => ({ q: x.q, a: x.a })), donts: d.draft.donts.join('\n'),
        note: 'AI 寫好草稿了，請核對後修改。' });
      W.dirty = true;
    }
    render();
  }

  async function tplSave(approve) {
    const t = W.tpl;
    if (!t || W.busy) return;
    readForm();
    const focus = (sel) => $id('wizardBody')?.querySelector(sel)?.focus();
    if (W.raw.p.name.trim().length < 2) { msg('請填商品名稱（至少 2 個字）。'); focus('[data-p="name"]'); return; }
    const by = one(t.by, 20);
    if (approve && !by) { msg('請先填審核人，再按「儲存並審核通過」。'); focus('[data-t="by"]'); return; }
    W.busy = true;
    try {
      const d = await H.api('/api/template/save', { id: t.id || undefined, template: tplBody(), approve, by });
      if (approve) { try { localStorage.setItem(REVIEWER_KEY, by); } catch { /* 存不了就下次再填 */ } }
      W.dirty = false;
      H.toast(d.template?.review?.status === 'approved' ? '已儲存（已審核）' : '已儲存（未審核）', 'info', 2500);
      H.go('scenarios');
    } catch (err) {
      msg(err.message || '儲存失敗');
      H.toast(esc(err.message || '儲存失敗'), 'error', 7000, '還不能儲存');
    } finally {
      W.busy = false;
    }
  }

  // 新增（id 空白）或打開一份商品範本
  async function openTemplate(id = '') {
    W.gen += 1;
    const my = W.gen;
    dropUpload();
    let by = '';
    try { by = localStorage.getItem(REVIEWER_KEY) || ''; } catch { /* 讀不到就空白 */ }
    const tpl = { id: '', points: '', qa: [], donts: '', review: { status: 'draft', by: '', at: '' }, text: '', source: '', busy: false, note: '', by, gen: 0 };
    const raw = blankRaw();
    if (id) {
      let t;
      try { t = (await H.api('/api/template/get', { id })).template; } catch (err) { H.toast(esc(err.message), 'error', 6000, '打不開這份範本'); return; }
      if (my !== W.gen) return;
      raw.type = TYPES[t.type] ? t.type : 'custom';
      FK.forEach((k) => { raw.p[k] = String(t.p?.[k] || ''); });
      Object.assign(tpl, { id: t.id, points: (t.points || []).join('\n'), qa: (t.qa || []).map((x) => ({ q: String(x.q || ''), a: String(x.a || '') })),
        donts: (t.donts || []).join('\n'), review: t.review || tpl.review, source: t.source || '' });
    }
    Object.assign(W, { step: 1, raw, phrases: [...PHRASES], editId: '', saved: null, base: null, dirty: false, busy: false, ol: null, note: null, cands: null, tpl });
    render();
    H.show('wizard');
    H.setNav('scenarios');
  }

  function step2() {
    const r = W.raw;
    const pr = r.pid ? persona(r.pid) : null;
    const av = r.av || pr?.av || 'chen';
    return `<div class="field"><span>客戶人設卡 <em class="wz-req">*</em></span><div class="wz-cards">${PERSONAS.map((p0) => personaFor(r.type, p0.id)).map((p) =>
      `<button type="button" class="wz-card${r.pid === p.id ? ' on' : ''}" data-act="persona" data-v="${p.id}" aria-pressed="${r.pid === p.id}">
        <span class="wz-card-av">${H.avatarSVG(p.av)}</span><span class="wz-card-main"><b>${esc(p.title)}</b><small>${esc(p.sub)}</small><span>${esc(p.pain)}</span></span></button>`).join('')}</div></div>
      <div class="form-grid">
        ${field('data-c', 'name', '姓名／稱呼', r.c.name, { max: 12, ph: pr?.name || '' })}
        ${field('data-c', 'age', '年齡', r.c.age, { max: 3, ph: pr?.age || '' })}
        ${field('data-c', 'job', '職業背景', r.c.job, { max: 30, ph: pr?.job || '' })}
        ${field('data-c', 'asset', '資產規模', r.c.asset, { max: 40, ph: pr?.asset || '' })}
        <div class="field wide"><span>頭像（決定通話時的性別和聲音）</span><div class="av-pick">${['chen', 'lin', 'wang'].map((a) =>
          `<button type="button" data-act="av" data-v="${a}" class="${av === a ? 'on' : ''}" aria-pressed="${av === a}">${H.avatarSVG(a)}</button>`).join('')}<span class="muted">${av === 'chen' ? '男' : '女'}</span></div></div>
      </div>
      <div class="field"><span>口頭禪（可複選，客戶會在對話裡講出來）</span><div class="wz-tags">${W.phrases.filter((t) => fitsType(r.type, t) || r.ph.includes(t)).map((t) =>
        `<button type="button" class="wz-tag${r.ph.includes(t) ? ' on' : ''}" data-act="phrase" data-v="${esc(t)}" aria-pressed="${r.ph.includes(t)}">「${esc(t)}」</button>`).join('')}</div>
        <input type="text" id="wzNewPhrase" maxlength="40" placeholder="輸入自己的口頭禪，按 Enter 新增" autocomplete="off"></div>
      <div class="field"><span>刁難度 <em class="wz-req">*</em></span><div class="wz-diff">${DIFF_OPTS.map(([n, t, s]) =>
        `<button type="button" class="wz-dopt d${n}${r.d === n ? ' on' : ''}" data-act="diff" data-v="${n}" aria-pressed="${r.d === n}"><b>${t}</b><small>${s}</small></button>`).join('')}</div></div>
      <p class="wz-msg" id="wzMsg" role="alert"></p>
      <div class="form-foot"><button class="pill ghost" type="button" data-act="prev">上一步</button><button class="pill primary" type="button" data-act="next">下一步：確認大綱</button></div>`;
  }

  function step3() {
    const ctx = normalize(W.raw);
    const keep = W.base && recipeKey(W.base.ctx) === recipeKey(ctx);
    const ol = W.ol || (keep ? W.base.ol : buildOutline(ctx));
    const part = (k, t) => `<div class="wz-ol"><h4>${t}</h4><ul contenteditable="true" spellcheck="false" data-ol="${k}">${ol[k].map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
    return `<div class="wz-outline"><p class="muted">這是這個情境的大綱，文字可以直接點進去改。確認後按「${W.editId ? '儲存修改' : '建立情境'}」。</p>
        ${part('prod', '商品核心規格與亮點重點')}${part('cust', '客戶態樣背景與核心痛點')}${part('atk', '本次對練核心攻防情境')}</div>
      <div class="form-grid">
        <label class="field"><span>製作單位 <em class="wz-req">*</em></span><select data-w="unit"><option value="">請選擇單位</option>${UNITS.map((u) => `<option${W.raw.w.unit === u ? ' selected' : ''}>${u}</option>`).join('')}</select></label>
        ${field('data-w', 'name', '窗口姓名', W.raw.w.name, { max: 20, ph: '請輸入窗口姓名', req: true })}
      </div>
      <p class="wz-msg" id="wzMsg" role="alert"></p>
      <div class="form-foot"><button class="pill ghost" type="button" data-act="prev">上一步</button><button class="pill primary" type="button" data-act="save">${W.editId ? '儲存修改' : '建立情境'}</button></div>`;
  }

  // ---- 建立之後：完整劇本
  function result() {
    const { ctx, ol, rows, title, note, fresh } = W.saved;
    const chat = `<div class="wz-chat">${rows.map((r) => (r.m
      ? `<div class="wz-round" data-m="1" data-s="${esc(r.s)}">${esc(r.l)}</div>`
      : `<div class="wz-row ${r.w === 'A' ? 'adv' : 'cust'}" data-s="${esc(r.s)}" data-w="${r.w}"><div class="wz-who">${r.w === 'A' ? '永豐超業小豐' : esc(ctx.c.name)}</div><div class="wz-bub" contenteditable="true" spellcheck="false">${esc(r.t)}</div></div>`)).join('')}</div>`;
    const sec = (i, body, open) => `<details class="wz-sec"${open ? ' open' : ''}><summary>${esc(SECTIONS[i][1])}</summary><div class="wz-doc">${body}</div></details>`;
    return `<div class="wz-done"><b>${fresh ? '情境已經存好：' : ''}${esc(title)}</b><span>${esc(note)}</span>
        <div class="wz-acts">
          <button class="pill primary" type="button" data-act="call">馬上對練</button>
          <button class="pill" type="button" data-act="word">下載 Word</button>
          <button class="pill" type="button" data-act="export">匯出情境檔</button>
          <button class="pill ghost" type="button" data-act="again">回去改內容</button>
        </div></div>
      ${sec(0, sec1(ctx, ol, false), false)}
      <details class="wz-sec" open><summary>${esc(SECTIONS[1][1])}</summary><div class="wz-doc">
        <p class="muted">對話的文字可以直接點進去改，改完按下面的「儲存對話修改」；下載的 Word 會用改過的內容。</p>${chat}
        <div class="wz-chat-foot"><button class="pill" type="button" data-act="saverows" disabled>儲存對話修改</button></div></div></details>
      ${sec(2, sec3(ctx, false), false)}${sec(3, sec4(ctx, rows, false), false)}
      <div class="form-foot"><button class="pill ghost" type="button" data-act="list">回情境設定</button></div>`;
  }

  function render() {
    const box = $id('wizardBody');
    if (!box) return;
    if (W.tpl) {
      $id('wizardTitle').textContent = W.tpl.id ? '修改商品範本' : '新增商品範本';
      box.innerHTML = tplPage();
      return;
    }
    $id('wizardTitle').textContent = W.step === 4 ? 'Role Play 劇本' : W.editId ? '修改情境' : '新增情境';
    box.innerHTML = W.step === 4 ? result() : stepsBar() + [null, step1, step2, step3][W.step]();
  }

  const msg = (t) => { const m = $id('wzMsg'); if (m) m.textContent = t || ''; };

  function goStep(n) {
    readForm();
    if (W.step === 3) W.ol = readOutline(); // 大綱改到一半先記著，回來還在
    if (n > 1 && !W.raw.p.name.trim()) { W.step = 1; render(); msg('請填寫商品名稱。'); return; }
    if (n > 2 && !W.raw.pid) { W.step = 2; render(); msg('請選一張客戶人設卡。'); return; }
    if (n === 3 && W.step < 3) W.ol = null; // 前面改過了：大綱照新的內容重新產生
    if (n !== 1) dropUpload(); // 離開第一步：不等 AI 了，照現在填的內容往下走
    W.step = n;
    render();
    document.querySelector('.main')?.scrollTo?.(0, 0);
  }

  async function save() {
    if (W.busy) return;
    readForm();
    const olNow = readOutline();
    if (!W.raw.w.unit || !W.raw.w.name.trim()) { msg('請選擇製作單位並填寫窗口姓名。'); return; }
    const ctx = normalize(W.raw);
    const ol = cleanOutline(olNow, ctx);
    // 填的內容沒變就留著改過的對話；有變就照新的內容重新產生
    let rows = buildChat(ctx);
    if (W.base && recipeKey(W.base.ctx) === recipeKey(ctx)) rows = W.base.rows;
    else if (W.base && JSON.stringify(W.base.rows) !== JSON.stringify(buildChat(W.base.ctx))
      && !window.confirm('你改過商品或人設，超業範本對話會照新的內容重新產生，之前手動改過的對話會被蓋掉。要繼續嗎？')) return;
    try { localStorage.setItem(WINDOW_KEY, JSON.stringify(ctx.w)); } catch { /* 存不了就下次再填 */ }
    await put(ctx, ol, rows, W.editId ? '已儲存修改' : '情境已建立');
  }

  async function put(ctx, ol, rows, okText) {
    W.busy = true;
    try {
      const scenario = toScenario(ctx, ol, rows);
      const isNew = !W.editId;
      const d = await H.api('/api/editor/save', { id: W.editId || undefined, scenario });
      W.editId = d.id;
      W.base = { ctx, ol, rows };
      W.saved = {
        id: d.id, ctx, ol, rows, title: `${ctx.c.name} · ${scenario.title}`, fresh: true,
        note: isNew ? '首頁已經多了這位客戶，可以馬上對練。' : '修改已經存好，下一通電話開始生效。',
      };
      W.dirty = false;
      W.ol = null;
      W.step = 4;
      await H.reloadScenarios().catch(() => {});
      render();
      document.querySelector('.main')?.scrollTo?.(0, 0);
      H.toast(okText, 'info', 2500);
    } catch (err) {
      msg(err.message || '儲存失敗');
      H.toast(esc(err.message || '儲存失敗'), 'error', 7000, '儲存失敗');
    } finally {
      W.busy = false;
    }
  }

  function download(name, content, type) {
    const blob = new Blob([content], { type });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  const safeName = (s) => String(s || '').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 30) || '情境';
  const saveWord = (product, persona, html) => download(wordName(product, persona), `﻿${html}`, 'application/msword');
  const saveJson = (pb, title, name) => download(`情境檔_${safeName(name)}_${stamp()}.json`, JSON.stringify(exportData(pb, title, new Date().toISOString()), null, 2), 'application/json');

  function onClick(e) {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const act = b.dataset.act;
    const r = W.raw;
    if (act === 'step') { goStep(Number(b.dataset.n)); return; }
    if (act === 'next') { goStep(W.step + 1); return; }
    if (act === 'prev') { goStep(W.step - 1); return; }
    if (act === 'blank') { if (!W.dirty || window.confirm('要放棄這裡填的內容，改用空白表單嗎？')) { W.dirty = false; H.openEditor(''); } return; }
    if (act === 'type') {
      readForm();
      applyType(r, b.dataset.v);
      W.dirty = true;
      render();
      return;
    }
    if (act === 'persona') {
      readForm();
      const pr = persona(b.dataset.v);
      r.pid = pr.id;
      r.av = pr.av;
      r.c = { name: pr.name, age: pr.age, job: pr.job, asset: pr.asset }; // 換人設卡就帶入這張卡的預設，跟生成器一樣
      W.dirty = true;
      render();
      return;
    }
    if (act === 'av') { readForm(); r.av = b.dataset.v; W.dirty = true; render(); return; }
    if (act === 'phrase') { const t = b.dataset.v; r.ph = r.ph.includes(t) ? r.ph.filter((x) => x !== t) : r.ph.concat(t); b.classList.toggle('on'); b.setAttribute('aria-pressed', String(r.ph.includes(t))); W.dirty = true; return; }
    if (act === 'diff') { readForm(); r.d = Number(b.dataset.v); W.dirty = true; render(); return; }
    if (act === 'save') { save(); return; }
    // ---- 商品範本
    if (act === 'tpl-ai') { tplAi(); return; }
    if (act === 'tpl-save') { tplSave(false); return; }
    if (act === 'tpl-approve') { tplSave(true); return; }
    if (act === 'tpl-qa-add') {
      readForm();
      if (W.tpl.qa.length < TPL_QA_MAX) W.tpl.qa.push({ q: '', a: '' });
      render();
      $id('wizardBody').querySelector('.tpl-qa-row:last-child [data-qa="q"]')?.focus();
      return;
    }
    if (act === 'tpl-qa-del') {
      readForm(); // 畫面上有幾組，W.tpl.qa 就有幾組（空的也算），第幾組才對得上
      W.tpl.qa.splice(Number(b.closest('.tpl-qa-row').dataset.i), 1);
      W.dirty = true;
      render();
      return;
    }
    // ---- 劇本頁
    const s = W.saved;
    if (act === 'call') { if (confirmRows()) H.startCall(s.id); return; }
    if (act === 'word') { saveWord(s.ctx.p.name, s.ctx.pr.title, docHtml(s.ctx, s.ol, cleanRows(readRows(), s.ctx), today())); return; }
    if (act === 'export') { if (confirmRows()) saveJson(toScenario(s.ctx, s.ol, s.rows).playbook, s.title, s.ctx.c.name); return; }
    if (act === 'again') { if (confirmRows()) { W.step = 1; render(); } return; }
    if (act === 'saverows') { put(s.ctx, s.ol, cleanRows(readRows(), s.ctx), '對話已儲存'); return; }
    if (act === 'list') { H.go('scenarios'); }
  }
  // 劇本頁的對話改過還沒存：先問一次；同意就不再擋（後面換頁不用再問第二次）
  const confirmRows = () => {
    if (W.dirty && !window.confirm('對話改過還沒儲存，要先不管它繼續嗎？')) return false;
    W.dirty = false;
    return true;
  };

  function wire() {
    const box = $id('wizardBody');
    box.addEventListener('click', onClick);
    box.addEventListener('input', (e) => {
      if (W.step === 4) {
        if (e.target.closest('.wz-bub')) { W.dirty = true; const b = box.querySelector('[data-act="saverows"]'); if (b) b.disabled = false; }
        return;
      }
      W.dirty = true;
      msg('');
    });
    box.addEventListener('change', (e) => {
      if (e.target.id === 'wzFile') { takeFile(e.target.files[0]); e.target.value = ''; }
      if (e.target.id === 'wzPick') pickCandidate(Number(e.target.value));
    });
    box.addEventListener('keydown', (e) => {
      if (e.target.id === 'wzNewPhrase' && e.key === 'Enter') {
        e.preventDefault();
        const t = one(e.target.value.replace(/^[「"]|[」"]$/g, ''), 40);
        if (t && !W.phrases.includes(t)) W.phrases.push(t);
        if (t && !W.raw.ph.includes(t)) W.raw.ph.push(t);
        readForm();
        W.dirty = true;
        render();
        $id('wzNewPhrase')?.focus();
        return;
      }
      // 大綱和對話是整塊可以打字的：Enter 在大綱裡換一行（瀏覽器會自己加一個項目），其他不攔
    });
    ['dragenter', 'dragover'].forEach((ev) => box.addEventListener(ev, (e) => { const z = e.target.closest('#wzDrop'); if (z) { e.preventDefault(); z.classList.add('drag'); } }));
    ['dragleave', 'drop'].forEach((ev) => box.addEventListener(ev, (e) => {
      const z = e.target.closest('#wzDrop');
      if (!z) return;
      e.preventDefault();
      z.classList.remove('drag');
      if (ev === 'drop' && e.dataTransfer?.files?.length) takeFile(e.dataTransfer.files[0]);
    }));
    $id('btnWizardBack').addEventListener('click', () => H.go('scenarios'));
  }

  // ---- 給 app.js 用的
  function init(hooks) {
    H = hooks;
    wire();
  }

  // 新增（id 空白）或重開一個三步驟做的情境
  async function open(id = '') {
    const my = ++W.gen;
    dropUpload();
    Object.assign(W, { step: 1, raw: blankRaw(), phrases: [...PHRASES], editId: '', saved: null, base: null, dirty: false, busy: false, ol: null, note: null, cands: null, tpl: null });
    if (id) {
      let sc;
      try { sc = (await H.api('/api/editor/get', { id })).scenario; } catch (err) { H.toast(esc(err.message), 'error', 6000, '打不開這個情境'); return; }
      if (my !== W.gen) return;
      if (!sc.playbook) { H.openEditor(id); return; }
      const { ctx, ol, rows } = fromPlaybook(sc.playbook);
      W.raw = { type: ctx.type, p: { ...ctx.p }, pid: ctx.pid, av: ctx.av, c: { ...ctx.c, age: String(ctx.c.age) }, ph: [...ctx.ph], d: ctx.d, w: { ...ctx.w } };
      W.phrases = [...new Set(PHRASES.concat(ctx.ph))];
      W.editId = id;
      W.base = { ctx, ol, rows };
      W.saved = { id, ctx, ol, rows, title: `${ctx.c.name} · ${sc.title}`, fresh: false, note: '這個情境是用三步驟建立的，下面是它的劇本。' };
      W.step = 4; // 重開先看到劇本；要改內容按「回去改內容」
    }
    render();
    H.show('wizard');
    H.setNav('scenarios');
  }

  async function downloadDoc(id) {
    try {
      const sc = (await H.api('/api/editor/get', { id })).scenario;
      if (sc.playbook) {
        const { ctx, ol, rows } = fromPlaybook(sc.playbook);
        saveWord(ctx.p.name, ctx.pr.title, docHtml(ctx, ol, rows, today()));
      } else {
        const pub = H.scenario(id) || sc; // 首頁那份已經把「業務稱呼」這些代稱換成實際的字
        saveWord(pub.product, pub.customer?.name, plainDocHtml(pub, today())); // 不是三步驟做的：沒有客戶態樣，用客戶的名字
      }
    } catch (err) {
      H.toast(esc(err.message), 'error', 6000, '下載失敗');
    }
  }

  async function exportFile(id) {
    try {
      const sc = (await H.api('/api/editor/get', { id })).scenario;
      if (!sc.playbook) { H.toast('這個情境不是用三步驟建立的，沒有辦法匯出成情境檔。', 'warn', 6000); return; }
      saveJson(sc.playbook, sc.title, sc.customer?.name);
    } catch (err) {
      H.toast(esc(err.message), 'error', 6000, '匯出失敗');
    }
  }

  // 匯入情境檔：照檔案裡的內容重新產生一個情境，存成新的一筆
  async function importFile(file) {
    if (!file) return false;
    try {
      if (file.size > 300000) throw new Error('這個檔案太大，不像是情境檔');
      let data;
      try { data = JSON.parse(await file.text()); } catch { throw new Error('讀不懂這個檔案，請確認是從「匯出」存下來的情境檔'); }
      const { ctx, ol, rows } = readExport(data);
      const scenario = toScenario(ctx, ol, rows);
      await H.api('/api/editor/save', { scenario });
      await H.reloadScenarios().catch(() => {});
      H.toast(`已匯入：${esc(ctx.c.name)} · ${esc(scenario.title)}`, 'info', 3500);
      return true;
    } catch (err) {
      H.toast(esc(err.message), 'error', 7000, '匯入失敗');
      return false;
    }
  }

  return { ...logic, init, open, openTemplate, downloadDoc, exportFile, importFile, get dirty() { return W.dirty; }, discard() { W.dirty = false; dropUpload(); if (W.tpl) W.tpl.gen += 1; } };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = Arena;
