import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { StyleProvider, legacyLogicalPropertiesTransformer } from '@ant-design/cssinjs'
import App from './App'
import './index.css'
import applyGapPolyfill from './utils/flexGapPolyfill'

function initPolyfill() {
  applyGapPolyfill()
  setTimeout(applyGapPolyfill, 500)
  setTimeout(applyGapPolyfill, 2000)
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initPolyfill)
} else {
  initPolyfill()
}

var root = document.getElementById('root')
ReactDOM.createRoot(root).render(
  <StyleProvider hashPriority="high" transformers={[legacyLogicalPropertiesTransformer]}>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: { colorPrimary: '#1677ff' },
        components: {
          Button: { contentFontSize: 14 },
        },
      }}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </StyleProvider>
)
