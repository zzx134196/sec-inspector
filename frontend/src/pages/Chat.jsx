import React, { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Layout, Input, Button, Typography, Avatar, Space, Dropdown, Tag, Spin, Upload, message, Tooltip
} from 'antd'
import {
  SendOutlined, PlusOutlined, SettingOutlined, LogoutOutlined,
  SafetyCertificateOutlined, UserOutlined, RobotOutlined, MenuFoldOutlined,
  MenuUnfoldOutlined, BugOutlined, FileSearchOutlined, AuditOutlined,
  ToolOutlined, CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined,
  BookOutlined, DatabaseOutlined, CloseOutlined, PaperClipOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import useAuthStore from '../stores/useAuthStore'
import { chatApi } from '../services/api'

const { Header, Sider, Content } = Layout
const { Text, Title, Paragraph } = Typography
const { TextArea } = Input

const EXAMPLE_QUESTIONS = [
  '帮我审核一下这份测评报告的身份鉴别部分',
  '请分析一下附件中的等保测评结论是否合理？',
  '查询CVE-2021-44228漏洞详情',
  '最近有哪些高危网络安全漏洞？',
]

const ATTACHMENT_MARKER = '\n\n【附件内容：'
const ATTACHMENT_END_MARKER = '】\n'

function parseMessageAttachment(content) {
  if (!content || typeof content !== 'string') {
    return { displayContent: content || '', attachment: null }
  }

  const markerIndex = content.indexOf(ATTACHMENT_MARKER)
  if (markerIndex === -1) {
    return { displayContent: content, attachment: null }
  }

  const attachmentBlock = content.slice(markerIndex + ATTACHMENT_MARKER.length)
  const attachmentEndIndex = attachmentBlock.indexOf(ATTACHMENT_END_MARKER)
  if (attachmentEndIndex === -1) {
    return { displayContent: content, attachment: null }
  }

  const name = attachmentBlock.slice(0, attachmentEndIndex).trim()
  const attachmentContent = attachmentBlock.slice(attachmentEndIndex + ATTACHMENT_END_MARKER.length).trim()

  return {
    displayContent: content.slice(0, markerIndex).trim(),
    attachment: name ? { name, content: attachmentContent, size: attachmentContent.length } : null,
  }
}

function normalizeMessage(msg) {
  if (!msg || msg.role !== 'user') return msg

  const { displayContent, attachment } = parseMessageAttachment(msg.content || '')
  if (!attachment) return msg

  return {
    ...msg,
    content: displayContent,
    attachment: attachment.name,
    attachmentMeta: attachment,
    rawContent: msg.content,
  }
}

export default function ChatPage() {
  const navigate = useNavigate()
  const { user, logout: doLogout } = useAuthStore()

  const [conversations, setConversations] = useState([])
  const [currentConvId, setCurrentConvId] = useState(null)
  const [messages, setMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [sideCollapsed, setSideCollapsed] = useState(false)
  const [streamStatus, setStreamStatus] = useState(null)
  const [uploadedFile, setUploadedFile] = useState(null)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)
  const abortRef = useRef(null)  // 用于取消流式请求

  useEffect(() => { loadConversations() }, [currentConvId])
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'auto' }) }, [messages, streamStatus])

  const loadConversations = async () => {
    try { setConversations(await chatApi.getConversations()) } catch (e) { /* ignore */ }
  }

  const loadMessages = async (convId) => {
    try {
      const history = await chatApi.getMessages(convId)
      setMessages(history.map(normalizeMessage))
      setUploadedFile(null)
      setCurrentConvId(convId)
    } catch (e) { /* ignore */ }
  }

  const handleNewChat = () => {
    setCurrentConvId(null)
    setMessages([])
    setInputValue('')
    setStreamStatus(null)
    setUploadedFile(null)
    inputRef.current?.focus()
  }

  const handleDeleteConv = async (convId) => {
    try {
      await chatApi.deleteConversation(convId)
      setConversations((prev) => prev.filter((c) => c.id !== convId))
      if (currentConvId === convId) handleNewChat()
    } catch (e) { /* ignore */ }
  }

  const handleSend = async (text) => {
    const msg = (text || inputValue).trim()
    if (!msg || loading) return

    setInputValue('')
    setLoading(true)

    const pendingAttachment = uploadedFile
    let finalMsg = msg
    if (pendingAttachment) {
      finalMsg = msg + ATTACHMENT_MARKER + pendingAttachment.name + ATTACHMENT_END_MARKER + pendingAttachment.content
    }

    const userMsgId = Date.now()
    const userMsg = normalizeMessage({ role: 'user', content: finalMsg, id: userMsgId })
    const aiMsgId = userMsgId + 1
    setMessages((prev) => [...prev, userMsg, { role: 'assistant', content: '', id: aiMsgId, streaming: true }])
    setStreamStatus({ thinking: true, toolCalls: [] })

    try {
      let fullReply = ''
      let finalData = null

      abortRef.current = new AbortController()
      await chatApi.sendMessageStream(
        { conversation_id: currentConvId, message: finalMsg },
        (chunk) => {
          if (chunk.type === 'thinking') {
            setStreamStatus((prev) => ({ ...prev, thinking: true, message: chunk.message }))
          } else if (chunk.type === 'thinking_content') {
            setMessages((prev) => prev.map((m) => m.id === aiMsgId ? { ...m, thinkingContent: (m.thinkingContent || '') + chunk.text } : m))
          } else if (chunk.type === 'tool_calling') {
            setStreamStatus((prev) => ({
              ...prev, thinking: false,
              toolCalls: [...(prev?.toolCalls || []), { tool: chunk.tool, args: chunk.args, status: 'running' }],
            }))
          } else if (chunk.type === 'tool_result') {
            setStreamStatus((prev) => ({
              ...prev,
              toolCalls: (prev?.toolCalls || []).map((tc, i) =>
                i === (prev?.toolCalls || []).length - 1
                  ? { ...tc, status: chunk.success ? 'success' : 'error', summary: chunk.summary }
                  : tc
              ),
            }))
          } else if (chunk.content) {
            fullReply += chunk.content
            setMessages((prev) => prev.map((m) => m.id === aiMsgId ? { ...m, content: fullReply } : m))
          } else if (chunk.done) {
            finalData = chunk.data
            if (chunk.conversation_id && !currentConvId) setCurrentConvId(chunk.conversation_id)
          }
        },
        abortRef.current.signal
      )

      setMessages((prev) => prev.map((m) => m.id === aiMsgId ? { ...m, streaming: false, metadata: finalData ? { data: finalData } : null } : m))
      setStreamStatus(null)
      setUploadedFile(null)
      loadConversations()
    } catch (e) {
      if (e?.name !== 'AbortError') {
        setMessages((prev) => prev.map((m) => m.id === aiMsgId ? { ...m, content: '请求失败，请检查网络连接和后端服务。', streaming: false } : m))
      }
      setStreamStatus(null)
    } finally {
      abortRef.current = null
      setLoading(false)
    }
  }

  const handleUpload = async (options) => {
    const { file, onSuccess, onError } = options
    try {
      const res = await chatApi.uploadFile(file)
      onSuccess('ok')
      message.success('文件“' + file.name + '”解析成功，请输入您的需求后发送')
      setUploadedFile({ name: file.name, content: res.content })
    } catch (e) {
      onError(e)
      message.error('文件上传解析失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const handleLogout = () => { doLogout(); navigate('/login') }

  const userMenu = {
    items: [
      { key: 'logout', label: '退出登录', icon: <LogoutOutlined />, onClick: handleLogout },
    ],
  }

  return (
    <Layout style={{ height: '100vh' }}>
      {/* 左侧栏 */}
      <Sider
        width={260} collapsedWidth={0} collapsed={sideCollapsed} trigger={null}
        className="app-sidebar"
        style={{ overflow: 'auto' }}
      >
        <div className="sidebar-divider-bottom" style={{ padding: '14px 16px' }}>
          <Button
            icon={<PlusOutlined />} block onClick={handleNewChat}
            style={{ borderRadius: 8, height: 40, fontWeight: 500, background: 'rgba(255, 255, 255, 0.15)', color: '#fff', border: 'none' }}
          >
            新对话
          </Button>
        </div>

        {/* 历史对话 */}
        {conversations.length > 0 && (
          <>
            <div className="sidebar-divider-top" style={{ padding: '14px 16px 6px', marginTop: 4 }}>
              <Text style={{ fontSize: 11, fontWeight: 600, letterSpacing: 1 }}>历史对话</Text>
            </div>
            <div style={{ padding: '0 8px', maxHeight: 240, overflow: 'auto' }}>
              {conversations.slice(0, 20).map((conv) => (
                <div key={conv.id} style={{ display: 'flex', alignItems: 'center', marginBottom: 2, borderRadius: 4 }}>
                  <Button className={`sidebar-menu-btn ${conv.id === currentConvId ? 'active' : ''}`} type="text" size="small" style={{ flex: 1, textAlign: 'left', height: 32, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    onClick={() => loadMessages(conv.id)}>{conv.title}</Button>
                  <CloseOutlined style={{ fontSize: 10, color: '#999', padding: '0 6px', cursor: 'pointer', flexShrink: 0 }}
                    onClick={(e) => { e.stopPropagation(); handleDeleteConv(conv.id) }} />
                </div>
              ))}
            </div>
          </>
        )}

        {/* The admin entry section has been completely removed per user request */}
      </Sider>

      {/* 主内容区 */}
      <Layout>
        <Header style={{ background: '#fff', padding: '0 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #f0f0f0', height: 52, boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}>
          <div className="flex-gap-8" style={{ display: 'flex', alignItems: 'center' }}>
            <Button type="text" icon={sideCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setSideCollapsed(!sideCollapsed)} style={{ fontSize: 16 }} />
            <div className="flex-gap-8" style={{ display: 'flex', alignItems: 'center' }}>
              <div style={{ width: 28, height: 28, borderRadius: 6, background: 'linear-gradient(135deg, #1677ff, #4096ff)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <SafetyCertificateOutlined style={{ color: '#fff', fontSize: 15 }} />
              </div>
              <Title level={4} style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>等保测评助手</Title>
            </div>
          </div>
          <Dropdown menu={userMenu} placement="bottomRight">
            <div className="flex-gap-8" style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', padding: '4px 8px', borderRadius: 8 }}>
              <Avatar size={28} icon={<UserOutlined />} style={{ background: 'linear-gradient(135deg, #1677ff, #4096ff)' }} />
              <Text style={{ fontSize: 13 }}>{user?.display_name || user?.username}</Text>
              {user?.role === 'admin' && <Tag color="blue" style={{ margin: 0, fontSize: 11, lineHeight: '18px', height: 20 }}>管理员</Tag>}
            </div>
          </Dropdown>
        </Header>

        <Content style={{ display: 'flex', flexDirection: 'column', background: '#f5f6f8' }}>
          {/* 消息列表 */}
          <div style={{ flex: 1, overflow: 'auto', padding: '20px 24px 0' }}>
            {messages.length === 0 ? (
              <WelcomeScreen onSend={handleSend} />
            ) : (
              messages.map((msg, idx) => (<MessageBubble key={msg.id || idx} msg={msg} />))
            )}
            {streamStatus && <StreamStatusIndicator status={streamStatus} />}
            {loading && !streamStatus && <div style={{ textAlign: 'center', padding: 16 }}><Spin tip="思考中..." /></div>}
            <div ref={messagesEndRef} />
          </div>

          {/* 输入区 */}
          <div style={{ padding: '10px 24px 14px', background: '#fff', borderTop: '1px solid #eee', boxShadow: '0 -2px 8px rgba(0,0,0,0.03)' }}>
            <div style={{ maxWidth: 800, margin: '0 auto' }}>
              {uploadedFile && (
                <div className="flex-gap-8" style={{ display: 'flex', alignItems: 'center', marginBottom: 8, padding: '8px 12px', background: '#e6f4ff', borderRadius: 10, border: '1px solid #91caff', fontSize: 13, boxShadow: '0 1px 4px rgba(24,144,255,0.08)' }}>
                  <PaperClipOutlined style={{ color: '#1677ff', fontSize: 14 }} />
                  <span style={{ flex: 1, color: '#1f1f1f' }}>
                    已附加文件：<strong>{uploadedFile.name}</strong>
                    <span style={{ color: '#666', marginLeft: 6 }}>已解析 {uploadedFile.content?.length || 0} 字，发送后会随消息一起提交</span>
                  </span>
                  <CloseOutlined style={{ color: '#999', cursor: 'pointer', fontSize: 12 }} onClick={() => setUploadedFile(null)} />
                </div>
              )}
              <div className="flex-gap-10" style={{ display: 'flex', alignItems: 'flex-end', background: '#f7f8fa', borderRadius: 12, padding: '8px 8px 8px 14px', border: '1px solid #e8e8e8' }}>
                <Tooltip title="支持格式: PDF, DOCX, DOC, TXT (需包含文本内容)" placement="topLeft">
                  <Upload customRequest={handleUpload} showUploadList={false} accept=".pdf,.docx,.doc,.txt">
                    <Button type="text" icon={<PaperClipOutlined />} style={{ color: uploadedFile ? '#1677ff' : '#999', background: uploadedFile ? '#e6f4ff' : 'transparent', marginBottom: 2 }} />
                  </Upload>
                </Tooltip>
                <TextArea
                  ref={inputRef} value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)} onKeyDown={handleKeyDown}
                  placeholder="请输入您的问题...（Enter发送，Shift+Enter换行）"
                  autoSize={{ minRows: 1, maxRows: 4 }}
                  style={{ borderRadius: 6, border: 'none', background: 'transparent', resize: 'none', boxShadow: 'none', padding: '4px 0', fontSize: 14 }}
                  disabled={loading}
                />
                {loading ? (
                  <Button danger icon={<span style={{ fontSize: 14 }}>■</span>}
                    onClick={() => { if (abortRef.current) abortRef.current.abort() }}
                    style={{ height: 36, minWidth: 36, borderRadius: 8, flexShrink: 0, padding: '0 14px', fontWeight: 500 }}
                  >停止</Button>
                ) : (
                  <Button type="primary" icon={<SendOutlined />} onClick={() => handleSend()} disabled={!inputValue.trim()}
                    style={{ height: 36, minWidth: 36, borderRadius: 8, flexShrink: 0, padding: '0 14px', background: inputValue.trim() ? 'linear-gradient(135deg, #1677ff, #4096ff)' : undefined, border: 'none', boxShadow: inputValue.trim() ? '0 2px 6px rgba(22,119,255,0.3)' : 'none' }}
                  >发送</Button>
                )}
              </div>
            </div>
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}


// ========== 子组件 ==========

function WelcomeScreen({ onSend }) {
  const features = [
    { icon: <AuditOutlined />, color: '#1677ff', bg: '#e6f4ff', title: '测评报告审核', desc: '上传测评文档，智能审查要素完整性与高风险判定' },
    { icon: <BugOutlined />, color: '#f5222d', bg: '#fff1f0', title: '漏洞信息检索', desc: '查询NVD/CVE漏洞库，获取最新重点漏洞情报' },
  ]

  return (
    <div style={{ maxWidth: 640, margin: '60px auto', textAlign: 'center' }}>
      <div style={{ marginBottom: 8 }}>
        <div style={{ width: 56, height: 56, borderRadius: 14, margin: '0 auto 16px', background: 'linear-gradient(135deg, #1677ff 0%, #4096ff 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 4px 16px rgba(22,119,255,0.25)' }}>
          <SafetyCertificateOutlined style={{ color: '#fff', fontSize: 28 }} />
        </div>
        <Title level={3} style={{ color: '#1a1a1a', marginBottom: 4, fontWeight: 600 }}>等保测评助手</Title>
        <Paragraph type="secondary" style={{ fontSize: 14, marginBottom: 0 }}>智能审核报告 · 检索国标法规 · 查询漏洞信息 · 参考描述模板</Paragraph>
      </div>

      <div style={{ margin: '28px 0' }}>
        <Text type="secondary" style={{ fontSize: 12 }}>您可以这样问我：</Text>
        <div className="flex-gap-8" style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', marginTop: 10 }}>
          {EXAMPLE_QUESTIONS.map((q, i) => (
            <Button key={i} size="small" style={{ borderRadius: 16, fontSize: 12, height: 30, padding: '0 14px', borderColor: '#e8e8e8', background: '#fff' }}
              onClick={() => onSend(q)}>{q}</Button>
          ))}
        </div>
      </div>

      <div className="welcome-grid" style={{ textAlign: 'left' }}>
        {features.map((item, i) => (
          <div key={i} className="flex-gap-12" style={{ padding: '14px 16px', borderRadius: 10, cursor: 'default', background: '#fff', border: '1px solid #f0f0f0', display: 'flex', alignItems: 'center', transition: 'all 0.25s' }}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = item.color; e.currentTarget.style.boxShadow = `0 2px 12px ${item.color}18` }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = '#f0f0f0'; e.currentTarget.style.boxShadow = 'none' }}
          >
            <div style={{ width: 38, height: 38, borderRadius: 10, background: item.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, fontSize: 18, color: item.color }}>{item.icon}</div>
            <div><Text strong style={{ fontSize: 13 }}>{item.title}</Text><br /><Text type="secondary" style={{ fontSize: 11 }}>{item.desc}</Text></div>
          </div>
        ))}
      </div>
    </div>
  )
}


function formatMessageContent(content) {
  if (!content || typeof content !== 'string') return content || ''
  var text = content

  text = text.replace(/```json\s*([\s\S]*?)```/g, function(match, jsonStr) {
    try {
      var obj = JSON.parse(jsonStr.trim())
      return '```json\n' + JSON.stringify(obj, null, 2) + '\n```'
    } catch (e) {
      return match
    }
  })

  text = text.replace(/(?:^|\n)(\{[\s\S]*?\})(?:\n|$)/g, function(match, jsonStr) {
    if (jsonStr.length < 50) return match
    try {
      var obj = JSON.parse(jsonStr.trim())
      return '\n```json\n' + JSON.stringify(obj, null, 2) + '\n```\n'
    } catch (e) {
      return match
    }
  })

  text = text.replace(/(?:^|\n)(\[[\s\S]*?\])(?:\n|$)/g, function(match, jsonStr) {
    if (jsonStr.length < 50) return match
    try {
      var obj = JSON.parse(jsonStr.trim())
      return '\n```json\n' + JSON.stringify(obj, null, 2) + '\n```\n'
    } catch (e) {
      return match
    }
  })

  return text
}

function MessageBubble({ msg }) {
  const isUser = msg.role === 'user'
  const isStreaming = !msg.content && !!msg.thinkingContent
  const [thinkingExpanded, setThinkingExpanded] = React.useState(true)
  const thinkingRef = React.useRef(null)
  const displayContent = React.useMemo(function() {
    return formatMessageContent(msg.content)
  }, [msg.content])

  // 开始流式输出时自动展开
  React.useEffect(() => {
    if (isStreaming) {
      setThinkingExpanded(true)
    }
  }, [isStreaming])

  // 内容更新时自动滚动到底部
  React.useEffect(() => {
    if (thinkingExpanded && thinkingRef.current) {
      thinkingRef.current.scrollTop = thinkingRef.current.scrollHeight
    }
  }, [msg.thinkingContent, thinkingExpanded])

  return (
    <div style={{
      display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start',
      marginBottom: 16, maxWidth: 800, margin: '0 auto 16px',
    }}>
      {!isUser && (
        <Avatar icon={<RobotOutlined />} size={34}
          style={{ background: 'linear-gradient(135deg, #1677ff, #4096ff)', marginRight: 10, flexShrink: 0, boxShadow: '0 2px 6px rgba(22,119,255,0.2)' }} />
      )}
      <div style={{
        maxWidth: '78%', padding: '12px 16px',
        borderRadius: isUser ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
        background: isUser ? 'linear-gradient(135deg, #1677ff 0%, #4096ff 100%)' : '#fff',
        color: isUser ? '#fff' : '#333',
        boxShadow: isUser ? '0 2px 8px rgba(22,119,255,0.2)' : '0 1px 6px rgba(0,0,0,0.06)',
        whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.7, fontSize: 14,
      }}>
        {isUser && msg.attachment && (
          <div className="flex-gap-6" style={{ marginBottom: msg.content ? 8 : 0, display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRadius: 999, background: 'rgba(255,255,255,0.18)', border: '1px solid rgba(255,255,255,0.28)', fontSize: 12 }}>
            <PaperClipOutlined />
            <span>已附带附件：{msg.attachment}</span>
          </div>
        )}
        {/* 思考过程展示（支持收起展开与自动滚动） */}
        {!isUser && msg.thinkingContent && (
          <div style={{ marginBottom: 10 }}>
            <div
              onClick={() => setThinkingExpanded(!thinkingExpanded)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '5px 10px', borderRadius: thinkingExpanded ? '8px 8px 0 0' : '8px',
                background: '#f0f4ff', border: '1px solid #e0e8ff',
                borderBottom: thinkingExpanded ? 'none' : '1px solid #e0e8ff',
                color: '#5b7cfa', fontSize: 12, fontWeight: 500, cursor: 'pointer',
                transition: 'all 0.2s',
              }}
            >
              <div className="flex-gap-6" style={{ display: 'flex', alignItems: 'center' }}>
                {isStreaming
                  ? <><Spin size="small" /><span style={{ marginLeft: 4 }}>思考中...</span></>
                  : <><span style={{ fontSize: 13 }}>💭</span><span>思考过程</span></>
                }
              </div>
              <div style={{ fontSize: 11, opacity: 0.8 }}>
                {thinkingExpanded ? '收起 ▲' : '展开 ▼'}
              </div>
            </div>
            {thinkingExpanded && (
              <div
                ref={thinkingRef}
                style={{
                  padding: '10px 12px',
                  background: '#f9fafe', border: '1px solid #e0e8ff', borderTop: 'none',
                  borderRadius: '0 0 8px 8px', fontSize: 12, color: '#666',
                  lineHeight: 1.8, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  maxHeight: 250, overflow: 'auto',
                }}>
                {msg.thinkingContent}
              </div>
            )}
          </div>
        )}
        {isUser ? (msg.content || (msg.attachment ? '请结合我上传的附件进行分析。' : '')) : (
          <div style={{ whiteSpace: 'normal', overflowWrap: 'break-word', wordBreak: 'break-word' }}>
            <ReactMarkdown
              components={{
                h1: ({children}) => <h3 style={{margin: '12px 0 6px', fontSize: 16, fontWeight: 600}}>{children}</h3>,
                h2: ({children}) => <h4 style={{margin: '10px 0 4px', fontSize: 15, fontWeight: 600}}>{children}</h4>,
                h3: ({children}) => <h5 style={{margin: '8px 0 4px', fontSize: 14, fontWeight: 600}}>{children}</h5>,
                p: ({children}) => <p style={{margin: '4px 0', lineHeight: 1.7, overflowWrap: 'break-word', wordBreak: 'break-word'}}>{children}</p>,
                ul: ({children}) => <ul style={{margin: '4px 0', paddingLeft: 20}}>{children}</ul>,
                ol: ({children}) => <ol style={{margin: '4px 0', paddingLeft: 20}}>{children}</ol>,
                li: ({children}) => <li style={{margin: '2px 0'}}>{children}</li>,
                strong: ({children}) => <strong style={{fontWeight: 600}}>{children}</strong>,
                table: ({children}) => <div style={{overflowX: 'auto', maxWidth: '100%', margin: '8px 0'}}><table style={{borderCollapse: 'collapse', fontSize: 12, width: '100%', minWidth: 300}}>{children}</table></div>,
                th: ({children}) => <th style={{border: '1px solid #e8e8e8', padding: '6px 10px', background: '#fafafa', fontWeight: 600, textAlign: 'left', whiteSpace: 'nowrap'}}>{children}</th>,
                td: ({children}) => <td style={{border: '1px solid #e8e8e8', padding: '6px 10px'}}>{children}</td>,
                blockquote: ({children}) => <blockquote style={{borderLeft: '3px solid #1677ff', margin: '8px 0', paddingLeft: 12, color: '#666'}}>{children}</blockquote>,
                pre: ({children}) => <pre style={{background: '#f5f5f5', padding: 10, borderRadius: 6, overflowX: 'auto', maxWidth: '100%', fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word'}}>{children}</pre>,
                code: ({inline, className, children}) => {
                  if (inline) return <code style={{background: '#f5f5f5', padding: '1px 4px', borderRadius: 3, fontSize: '0.9em', wordBreak: 'break-all'}}>{children}</code>
                  return <code style={{fontFamily: 'Consolas, Monaco, "Courier New", monospace'}}>{children}</code>
                },
              }}
            >{displayContent}</ReactMarkdown>
          </div>
        )}
        {msg.metadata?.data && <StructuredDataCard data={msg.metadata.data} />}
      </div>
      {isUser && (
        <Avatar icon={<UserOutlined />} size={34}
          style={{ background: 'linear-gradient(135deg, #52c41a, #73d13d)', marginLeft: 10, flexShrink: 0, boxShadow: '0 2px 6px rgba(82,196,26,0.2)' }} />
      )}
    </div>
  )
}


function StreamStatusIndicator({ status }) {
  return (
    <div style={{ marginBottom: 16, marginLeft: 44 }}>
      {status.thinking && (
        <div className="flex-gap-8" style={{ display: 'flex', alignItems: 'center', color: '#999', fontSize: 13, marginBottom: 6 }}>
          <LoadingOutlined spin />
          <span>{status.message || '正在思考...'}</span>
        </div>
      )}
      {(status.toolCalls || []).map((tc, i) => (
        <div key={i} className="tool-call-card">
          <Space>
            <ToolOutlined />
            <span className="tool-name">{TOOL_NAMES[tc.tool] || tc.tool}</span>
            {tc.status === 'running' && <LoadingOutlined spin className="tool-status" style={{ color: '#1677ff' }} />}
            {tc.status === 'success' && <CheckCircleOutlined className="tool-status" style={{ color: '#52c41a' }} />}
            {tc.status === 'error' && <CloseCircleOutlined className="tool-status" style={{ color: '#ff4d4f' }} />}
          </Space>
          {tc.summary && <div style={{ color: '#666', marginTop: 4, fontSize: 12 }}>{tc.summary}</div>}
        </div>
      ))}
    </div>
  )
}


function StructuredDataCard({ data }) {
  if (!data) return null
  const type = data.type

  if (type === 'audit_result' && data.audit) {
    const audit = data.audit
    const issues = audit.issues || []
    const highlights = (audit.highlights || []).filter(Boolean)
    return (
      <div style={{ marginTop: 10, padding: 14, background: '#fff', borderRadius: 10, border: '1px solid #e8e8e8', boxShadow: '0 1px 4px rgba(0,0,0,0.04)' }}>
        {/* 总体结论区 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, paddingBottom: 10, borderBottom: '1px solid #f0f0f0' }}>
          <Tag color={audit.overall_result === '通过' ? 'green' : audit.overall_result === '需修改' ? 'orange' : 'red'} style={{ fontSize: 13, padding: '2px 10px' }}>
            {audit.overall_result}
          </Tag>
          <Text strong style={{ fontSize: 14 }}>评分: {audit.score ?? '暂无'}</Text>
        </div>
        {/* 高风险提醒 */}
        {audit.high_risk_warning && (
          <div style={{ padding: '8px 12px', background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: 6, marginBottom: 10, fontSize: 13, color: '#cf1322' }}>
            ⚠️ <strong>高风险提醒：</strong>{audit.high_risk_warning}
          </div>
        )}
        {/* 总体评价 */}
        {audit.summary && (
          <div style={{ fontSize: 13, color: '#555', marginBottom: 10, lineHeight: 1.7 }}>
            {audit.summary}
          </div>
        )}
        {/* 问题列表 */}
        {issues.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            <Text strong style={{ fontSize: 13, color: '#333' }}>问题清单（{issues.length} 项）</Text>
            {issues.map((issue, i) => (
              <div key={i} style={{ padding: '8px 0', borderBottom: '1px solid #f5f5f5', fontSize: 13 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <Tag color={issue.severity === 'high' ? 'red' : issue.severity === 'medium' ? 'orange' : 'blue'} style={{ fontSize: 11 }}>
                    {issue.severity === 'high' ? '高' : issue.severity === 'medium' ? '中' : '低'}
                  </Tag>
                  <Text strong style={{ fontSize: 12 }}>{issue.dimension}</Text>
                </div>
                <div style={{ color: '#333', marginBottom: 3 }}>{issue.description}</div>
                {issue.suggestion && <div style={{ color: '#1677ff', fontSize: 12 }}>💡 建议: {issue.suggestion}</div>}
                {issue.location && <div style={{ color: '#999', fontSize: 11, marginTop: 2 }}>📍 定位: {issue.location.slice(0, 100)}</div>}
              </div>
            ))}
          </div>
        )}
        {/* 亮点 */}
        {highlights.length > 0 && issues.length === 0 && (
          <div>
            <Text strong style={{ fontSize: 13, color: '#52c41a' }}>✅ 亮点</Text>
            {highlights.map((h, i) => (
              <div key={i} style={{ fontSize: 12, color: '#555', padding: '2px 0' }}>• {h}</div>
            ))}
          </div>
        )}
      </div>
    )
  }

  if (type === 'vulnerability_search' && data.vulnerabilities) {
    return (
      <div style={{ marginTop: 10, padding: 12, background: '#fff', borderRadius: 8, border: '1px solid #e8e8e8' }}>
        <Text strong style={{ fontSize: 13 }}>共找到 {data.total} 条漏洞，展示 {data.returned} 条</Text>
        {data.vulnerabilities.slice(0, 5).map((v, i) => (
          <div key={i} style={{ padding: '6px 0', borderBottom: '1px solid #f5f5f5', fontSize: 13 }}>
            <Space>
              <Tag color="blue">{v.cve_id}</Tag>
              <Tag color={getSeverityColor(v.cvss_severity)}>{v.cvss_severity} ({v.cvss_score})</Tag>
            </Space>
            <div style={{ color: '#666', marginTop: 2, fontSize: 12 }}>{(v.description || '').slice(0, 120)}...</div>
          </div>
        ))}
      </div>
    )
  }

  if (type === 'vulnerability_detail' && data.vulnerability) {
    const v = data.vulnerability
    return (
      <div style={{ marginTop: 10, padding: 12, background: '#fff', borderRadius: 8, border: '1px solid #e8e8e8' }}>
        <Space style={{ marginBottom: 6 }}>
          <Tag color="blue" style={{ fontSize: 13 }}>{v.cve_id}</Tag>
          <Tag color={getSeverityColor(v.cvss_severity)}>{v.cvss_severity} ({v.cvss_score})</Tag>
        </Space>
        <div style={{ fontSize: 13, marginBottom: 6 }}>{v.full_description || v.description}</div>
        {v.cwes?.length > 0 && <div style={{ fontSize: 12, color: '#666' }}>CWE: {v.cwes.join(', ')}</div>}
        {v.affected_products?.length > 0 && (
          <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
            影响产品: {v.affected_products.slice(0, 3).map((p) => `${p.vendor}/${p.product}`).join(', ')}
          </div>
        )}
      </div>
    )
  }

  if (type === 'standard_search' && data.sources?.length > 0) {
    return (
      <div style={{ marginTop: 10, padding: '8px 10px', background: '#f6ffed', border: '1px solid #b7eb8f', borderRadius: 8 }}>
        <div className="flex-gap-4" style={{ fontSize: 11, color: '#52c41a', fontWeight: 600, marginBottom: 6, display: 'flex', alignItems: 'center' }}>
          <span>📄</span><span>参考文档（{data.sources.length} 个）</span>
        </div>
        {data.sources.map((src, i) => (
          <div key={i} className="flex-gap-4" style={{ fontSize: 11, color: '#555', padding: '2px 0', display: 'flex', alignItems: 'flex-start' }}>
            <span style={{ color: '#52c41a', flexShrink: 0 }}>›</span>
            <span>{src}</span>
          </div>
        ))}
      </div>
    )
  }

  if (type === 'check_item' && data.templates) {
    return (
      <div style={{ marginTop: 10, padding: 12, background: '#fff', borderRadius: 8, border: '1px solid #e8e8e8' }}>
        <Text strong style={{ fontSize: 13 }}>找到 {data.count} 条参考描述模板</Text>
        {data.templates.slice(0, 3).map((t, i) => (
          <div key={i} style={{ padding: '8px 0', borderBottom: '1px solid #f5f5f5', fontSize: 13 }}>
            <Space style={{ marginBottom: 4 }}>
              <Tag>{t.object_type}</Tag>
              <Text strong>{t.control_point}</Text>
            </Space>
            <div style={{ color: '#333' }}>{(t.control_item || '').slice(0, 100)}</div>
            {t.compliant_desc && <div style={{ color: '#52c41a', fontSize: 12, marginTop: 2 }}>符合: {t.compliant_desc.slice(0, 80)}...</div>}
            {t.non_compliant_desc && <div style={{ color: '#ff4d4f', fontSize: 12 }}>不符合: {t.non_compliant_desc.slice(0, 80)}...</div>}
          </div>
        ))}
      </div>
    )
  }

  return null
}


// 工具名称映射
const TOOL_NAMES = {
  audit_report: '审核测评报告',
  search_standard: '检索国标法规',
  check_item: '查询参考描述',
  search_vulnerability: '搜索漏洞',
  get_vulnerability_detail: '获取漏洞详情',
  export_file: '导出文件',
}

function getSeverityColor(severity) {
  const map = { CRITICAL: '#f5222d', HIGH: '#fa541c', MEDIUM: '#faad14', LOW: '#52c41a' }
  return map[severity] || '#999'
}
