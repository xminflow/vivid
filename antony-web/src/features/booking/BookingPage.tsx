/**
 * 展厅预约登记表。对应小程序 pages/booking。
 *
 * 校验在前后端各做一遍：这里挡住明显错误，省一次往返；服务端才是权威
 * （app/models.py 的 AppointmentIn），所以提交失败时直接展示服务端给的中文提示。
 */
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, submitAppointment } from '@/shared/api'
import { FieldRow, NumberInput, SelectInput, TextInput, TextareaInput } from '@/shared/components/Field'
import { SubmitBar, SubmitDone } from '@/shared/components/SubmitBar'
import { showroom } from '@/features/home/content'
import { MAX_PARTY, MIN_PARTY, PURPOSES, VISITOR_TYPES, todayStr } from './content'

interface FormState {
  name: string
  phone: string
  visitorType: string
  visitDate: string
  partySize: string
  purpose: string
  note: string
}

const EMPTY: FormState = {
  name: '',
  phone: '',
  visitorType: '',
  visitDate: '',
  partySize: '2',
  // 这张表就是展厅的预约入口，来的都是要看展厅的，先替他选上
  purpose: '展厅参观',
  note: '',
}

export function BookingPage() {
  const navigate = useNavigate()

  const [form, setForm] = useState<FormState>(EMPTY)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  const today = useMemo(todayStr, [])
  const set = (k: keyof FormState) => (v: string) => setForm((f) => ({ ...f, [k]: v }))

  /** 返回第一条错误，没有则返回空串。顺序与表单从上到下一致，用户好定位 */
  function validate(): string {
    if (!form.name.trim()) return '请填写您的称呼'
    if (!/^1[3-9]\d{9}$/.test(form.phone.trim())) return '电话格式不正确'
    if (!form.visitorType) return '请选择来者身份'
    if (!form.visitDate) return '请选择到访日期'
    if (form.visitDate < today) return '到访日期不能早于今天'
    const size = Number(form.partySize)
    if (!Number.isInteger(size) || size < MIN_PARTY || size > MAX_PARTY) {
      return `到访人数请填 ${MIN_PARTY}-${MAX_PARTY} 之间的整数`
    }
    if (!form.purpose) return '请选择预约需求'
    return ''
  }

  async function handleSubmit() {
    const invalid = validate()
    if (invalid) {
      setError(invalid)
      return
    }

    setSubmitting(true)
    setError('')
    try {
      await submitAppointment({
        name: form.name.trim(),
        phone: form.phone.trim(),
        visitorType: form.visitorType,
        visitDate: form.visitDate,
        partySize: Number(form.partySize),
        purpose: form.purpose,
        note: form.note.trim(),
      })
      setDone(true)
    } catch (e) {
      // 409 是同号同日重复预约，服务端的提示比通用文案准确，直接用
      setError(e instanceof ApiError ? e.message : '提交失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  if (done) {
    return (
      <div className="mx-auto max-w-[760px] px-5 sm:px-10">
        <SubmitDone
          title="已收到您的预约"
          body={`${showroom.name} · ${form.visitDate}\n我们会尽快电话与您确认`}
          actionLabel="返回首页"
          onAction={() => navigate('/')}
        />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[760px] px-5 py-16 sm:px-10 sm:py-24">
      <header>
        <h1 className="text-[26px] tracking-display sm:text-[34px]">展厅预约</h1>
        <p className="mt-3 text-[14px] leading-loose text-grigio">
          {showroom.name} · {showroom.hours}。留下联系方式，我们会电话与您确认到访时间。
        </p>
      </header>

      <div className="mt-12 grid gap-x-8 gap-y-7 sm:grid-cols-2">
        <FieldRow label="您的称呼" required>
          <TextInput
            value={form.name}
            onChange={set('name')}
            placeholder="怎么称呼您"
            maxLength={40}
            autoComplete="name"
          />
        </FieldRow>

        <FieldRow label="联系电话" required>
          <TextInput
            value={form.phone}
            onChange={set('phone')}
            placeholder="11 位手机号"
            inputMode="tel"
            maxLength={11}
            autoComplete="tel"
          />
        </FieldRow>

        <FieldRow label="来者身份" required>
          <SelectInput
            value={form.visitorType}
            onChange={set('visitorType')}
            options={VISITOR_TYPES}
          />
        </FieldRow>

        <FieldRow label="到访日期" required>
          {/* min 交给浏览器挡一道，日期选择器里过去的日子直接不可选 */}
          <TextInput value={form.visitDate} onChange={set('visitDate')} type="date" min={today} />
        </FieldRow>

        <FieldRow label="到访人数" required>
          <NumberInput value={form.partySize} onChange={set('partySize')} unit="人" />
        </FieldRow>

        <FieldRow label="预约需求" required>
          <SelectInput value={form.purpose} onChange={set('purpose')} options={PURPOSES} />
        </FieldRow>

        <FieldRow
          label="备注"
          wide
          hint={`${form.note.length}/500`}
        >
          <TextareaInput
            value={form.note}
            onChange={set('note')}
            placeholder="还有什么想让我们知道的"
            maxLength={500}
            rows={4}
          />
        </FieldRow>
      </div>

      <SubmitBar
        submitting={submitting}
        label="提交预约"
        error={error}
        onSubmit={() => void handleSubmit()}
      />
    </div>
  )
}
