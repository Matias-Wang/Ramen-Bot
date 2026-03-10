# Flex Message

## 核心參數分類：

### 1. 容器層級參數 (Container Level)

決定卡片的最外層，決定卡片的大小與排列方式。

- type: bubble（單張卡片）或 carousel（多張輪播）。
- size: 決定卡片的寬度。可選值包括 nano, micro, kilo, mega, giga（由窄到寬）。
- direction: 內容排列方向，ltr（由左至右）或 rtl（由右至左）。
- header / hero / body / footer: 四個主要區塊的開關。你可以只用 body，也可以全部組合使用。

### 2. 卡片層級參數 (Bubble Level)

1. Header (標頭區塊)

用途：顯示卡片的類別、標籤或次要標題。通常位於最上方。

- 主要容器：box
- 常用參數：
  - layout: 必填，通常為 vertical。
  - backgroundColor: 區塊背景顏色（如 #f0f0f0）。
  - paddingAll: 內邊距，控制內容與邊框的距離。
  - contents: 放置 text 組件。

1. Hero (主視覺區塊)

用途：顯示店家的主照片或宣傳圖。

- 主要組件：image 或 video (本專案建議先用 image)
- image 專屬參數：
  - url: 圖片的 HTTPS 連結。
  - aspectRatio: 圖片比例（如 16:9, 4:3, 1:1）。
  - aspectMode: cover (填滿) 或 fit (完整呈現)。
  - size: 寬度比例（通常設為 full）。
  - action: 點擊圖片觸發的動作（如 uri 開啟大圖）。

1. Body (主體區塊)

用途：這是最核心的區塊，用於顯示店名、地址、口味標籤以及 AI 推薦文案。

- 主要容器：box
- 內部常用組件與參數：
  - Text (文字)：
  - text: 內容。
  - size: 大小（sm, md, xl, xxl）。
  - weight: 粗細（bold 或 regular）。
  - color: 顏色（如店名用黑，推薦文用灰）。
  - wrap: true (自動換行，地址必設)。
  - style: italic (斜體，常用於 AI 推薦語)。
- Separator (分隔線)：
  - margin: 與上方的間距（md, lg, xxl）。
  - color: 線條顏色。
- Icon (圖示)：
  - url: 小圖示連結（如五角星）。
  - size: 圖示大小。

1. Footer (頁尾區塊)

用途：放置互動按鈕，如「查看地圖」、「撥打電話」或「分享」。

- 主要組件：button
- button 專屬參數：
  - Action (動作)：
    - type: uri (開啟連結) 或 message (傳送文字)。
    - uri: Google Maps 的連結。
    - label: 按鈕上顯示的文字（如「開啟導航」）。
  - style: 按鈕樣式（link, primary, secondary）。
  - color: 按鈕主色調。
  - height: 按鈕高度（sm 或 md）。

1. 全域共通參數 (通用於 Box/Bubble)

無論在哪個區塊，這些參數都能控制視覺細節：

- Spacing (間距)：box 內的組件間距。
- Margin (外邊距)：組件與前一個組件的距離。
- CornerRadius (圓角)：bubble 或內部 box 的圓滑程度。
- Action (點擊觸發)：可以設定在整個 bubble 或 box 上，讓使用者點擊該區塊即觸發動作。

💡 設計建議流程：

- Hero: 放置一張精美的拉麵圖片（或暫用佔位圖）。
- Body:
  - 第一行：店名 (Bold/XL)。
  - 第二行：標籤 (地區/口味，顏色稍淡)。
  - 第三行：AI 推薦語 (加分隔線、斜體)。
- Footer: 放置一個導航按鈕。

