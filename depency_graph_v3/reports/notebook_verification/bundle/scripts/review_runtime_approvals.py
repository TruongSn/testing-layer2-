"""Read-only review of the existing VERIFIED graph; emits PENDING proposals only.

Never calls builder, compile_graph, resolve(), ToolEnv, or a model. Pure _match()
probes inspect selector behavior without creating an executable graph or approval.
"""
from __future__ import annotations
import copy
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lifemate_graph.common import (canonical, fields, object_sha, read_csv, read_json,
                                  schema_errors, sha256, write_csv, write_json)
from lifemate_graph.compiler import load_compiled
from lifemate_graph.runtime import BackwardDependencyResolver, EvidenceLedger, GoalContext, GroundedValue


# A review decision is keyed by actual contract, not sample ID or trace frequency.
# READY entries outside PILOT remain excluded from this proposal.
READY = {
    ('chat_ott.list_chats', 'chat_ott.read_chat', 'chat_id'): ('name', {}, 'LOW', 'Tên chat/nhóm được nêu rõ; source ID scalar; không có alternative branch hay producer arguments bắt buộc.'),
    ('contacts.search', 'contacts.get', 'contact_id'): ('name', {'query': {'subject_key': 'name'}}, 'LOW', 'Tra cứu giới hạn bằng đúng tên user; unique exact contact name; consumer chỉ READ chi tiết.'),
    ('contacts.search', 'sms.read_recent', 'contact_id'): ('name', {'query': {'subject_key': 'name'}}, 'LOW', 'Chỉ resolve bộ lọc contact_id khi goal khai báo required_optional hoặc Policy đã đề xuất field; không tự thêm bộ lọc cho yêu cầu đọc SMS chung.'),
    ('email.list', 'email.read', 'email_id'): ('subject', {}, 'NORMAL', 'Subject email được user nêu nguyên văn và duy nhất; không tự chọn email mới nhất hoặc đầu tiên.'),
    ('food_delivery.list_restaurants', 'food_delivery.get_menu', 'restaurant_id'): ('name', {}, 'LOW', 'Tên quán exact và duy nhất; producer không cần arguments; consumer READ menu, không đặt món.'),
    ('learning.list_courses', 'learning.log_progress', 'course_id'): ('name', {}, 'LOW', 'Khóa học có tên exact duy nhất; chỉ bổ sung course_id; minutes và WRITE phải từ Policy và đúng goal.'),
    ('library.search', 'library.reserve', 'book_id'): ('title', {'query': {'subject_key': 'title'}}, 'LOW', 'Có thể query bằng title user nêu và abstain nếu nhiều sách cùng title; reservation vẫn do Policy.'),
    ('maps.search_place', 'maps.save_place', 'place_id'): ('name', {'query': {'subject_key': 'name'}}, 'LOW', 'Có query name grounded; chỉ lưu địa điểm exact duy nhất; yêu cầu thêm khu vực/địa chỉ cần binding rõ, không chọn theo rank.'),
    ('music.list_tracks', 'music.add_to_playlist', 'playlist_id'): ('name', {}, 'NORMAL', 'Playlist name và track title phải nằm trong hai slot riêng cùng goal; cùng observation có thể cung cấp cả hai ID.'),
    ('music.list_tracks', 'music.add_to_playlist', 'track_id'): ('title', {}, 'NORMAL', 'Track title exact duy nhất; paired playlist_id có subject riêng; không dùng artist hoặc vị trí danh sách để đoán track.'),
    ('music.list_tracks', 'music.play', 'playlist_id'): ('name', {}, 'NORMAL', 'Resolver hỗ trợ explicit selected_alternatives; nếu cả hai branch cùng khả dụng mà chưa chọn branch thì abstain.'),
    ('music.list_tracks', 'music.play', 'track_id'): ('title', {}, 'NORMAL', 'Chỉ branch track_id được goal chọn rõ; playlist alternative hợp lệ đã đáp ứng thì không đọc thêm.'),
    ('news.search', 'news.save', 'article_id'): ('title', {'query': {'subject_key': 'title'}}, 'LOW', 'Query title user nêu và exact-bind kết quả; chỉ dùng một producer route, không đồng bật news.headlines cho cùng slot.'),
    ('notes.list', 'notes.append', 'note_id'): ('title', {}, 'NORMAL', 'Note title exact duy nhất; chỉ resolve note_id, nội dung append phải do Policy và goal-scoped binding.'),
    ('podcast_radio.search', 'podcast_radio.queue', 'episode_id'): ('title', {'query': {'subject_key': 'title'}}, 'LOW', 'Episode title exact duy nhất với query grounded; nếu cần phân biệt show bằng join thì ngoài phạm vi này và phải abstain.'),
    ('podcast_radio.search', 'podcast_radio.subscribe', 'show_id'): ('name', {'query': {'subject_key': 'name'}}, 'LOW', 'Show name exact duy nhất với query grounded; subscribe vẫn là Policy WRITE, không phải graph action.'),
    ('reminders.list', 'reminders.complete', 'reminder_id'): ('content', {}, 'NORMAL', 'Reminder content exact duy nhất; không chọn theo thứ tự hay relative time; completion WRITE từ Policy.'),
    ('shopping_list.get', 'shopping_list.check', 'item_id'): ('name', {}, 'LOW', 'Item name exact duy nhất; không xóa item và không dùng quantity làm identity; Policy quyết định đánh dấu.'),
    ('tasks.list', 'tasks.complete', 'task_id'): ('title', {}, 'NORMAL', 'Task title exact duy nhất; không suy task từ priority hoặc ordinal; Policy quyết định complete.'),
}

PILOT = {
    ('chat_ott.list_chats', 'chat_ott.read_chat', 'chat_id'),
    ('contacts.search', 'contacts.get', 'contact_id'),
    ('contacts.search', 'sms.read_recent', 'contact_id'),
    ('email.list', 'email.read', 'email_id'),
    ('food_delivery.list_restaurants', 'food_delivery.get_menu', 'restaurant_id'),
}

FIX = {
    ('appliances.list', 'appliances.start_cycle', 'appliance_id'): ('F_MEMBERSHIP', 'Resolve appliance_id chưa bảo đảm cycle thuộc appliances[].cycles[] của đúng thiết bị; membership predicate đang NEEDS_REVIEW và runtime chưa hỗ trợ.'),
    ('bill_payment.list_bills', 'bill_payment.pay', 'bill_id'): ('F_BILL_AMOUNT_GUARD', 'B042 additional_grounding_check áp amount guard cho bill_payment.pay trong yêu cầu tiền điện, nhưng tool này không có amount argument; valid bill_id/source vẫn bị block. Cần sửa guard theo contract trước pilot.'),
    ('contacts.search', 'email.create_draft', 'to'): ('F_ARRAY_TARGET', 'Source email scalar nhưng target to là array; resolver chặn collection construction và _match kiểm scalar bằng array schema sẽ MISMATCH.'),
    ('contacts.search', 'email.send', 'to'): ('F_ARRAY_TARGET', 'Source email scalar nhưng target to là array; chưa có per-recipient row/collection binding, dù technical validator chấp nhận item type.'),
    ('contacts.search', 'video_meeting.create', 'attendees'): ('F_ARRAY_TARGET', 'Source contact ID scalar nhưng attendees là array; resolver hiện abstain, không tự dựng danh sách attendees.'),
    ('flights.my_bookings', 'flights.cancel', 'booking_id'): ('F_NESTED_SUBJECT', 'Booking record chỉ có flight object/flight_id và passenger fields; số chuyến bay/ngày có nghĩa với user nằm trong flight.*. Matcher chỉ lookup flat record[k], chưa bind nested flight identity.'),
    ('flights.my_bookings', 'flights.check_in', 'booking_id'): ('F_NESTED_SUBJECT', 'Cần nested flight identity/date của đúng booking; flight object/passengers array không thể trở thành GroundedValue scalar từ user span.'),
    ('food_delivery.list_restaurants', 'food_delivery.place_order', 'restaurant_id'): ('F_ORDER_PARENT_ROWS', 'Parent restaurant_id có thể resolve nhưng items[].item_id/quantity vẫn chưa có guard gắn với menu của đúng quán; không bật một phần prerequisite để cho order thiếu row provenance đi qua.'),
    ('food_delivery.order_history', 'food_delivery.cancel_order', 'order_id'): ('F_ORDER_SUBJECT_JOIN', 'Record order không có tên quán trực tiếp; cần join restaurant_id với restaurant identity và kiểm trạng thái đang chờ. Chọn bằng address/time đơn lẻ không đủ cho pilot hủy đơn.'),
    ('household_chores.list_chores', 'household_chores.assign', 'chore_id'): ('F_ASSIGNEE_MEMBERSHIP', 'Chore ID scalar được hỗ trợ nhưng assignee phải nằm trong members[]; membership edge chưa được runtime hỗ trợ nên chưa bảo toàn contract assign.'),
    ('smart_home.get_status', 'smart_home.climate_set', 'device_id'): ('F_DEVICE_CLASS', 'Unique device name không bảo đảm type=climate; current matcher không áp consumer-specific device class constraint.'),
    ('smart_home.get_status', 'smart_home.fan_set', 'device_id'): ('F_DEVICE_CLASS', 'Unique device name không bảo đảm type=fan; selector allow-list không phải predicate bắt buộc type của consumer.'),
    ('smart_home.get_status', 'smart_home.light_set', 'device_id'): ('F_DEVICE_CLASS', 'Current matcher nhận ID của record type=fan nếu name khớp, dù consumer là light_set. Cần ràng buộc class thực thi được.'),
    ('smart_home.get_status', 'smart_home.switch_set', 'device_id'): ('F_DEVICE_CLASS', 'Cần ràng buộc tập device types đúng contract switch_set (plug/curtain/TV); compiler hiện chỉ map subject_key, không có constant/enum predicate cho producer hoặc selected record.'),
}

REVIEW = {
    ('alarm.list','alarm.delete','alarm_id'): ('R_DESTRUCTIVE_TIME','Xóa báo thức cần đúng label/time/repeat; relative time và ý định xóa không được chứng minh bởi unique label đơn lẻ.'),
    ('alarm.list','alarm.update','alarm_id'): ('R_TEMPORAL_EDIT','Cần xác minh thời gian/repeat của báo thức và thời gian mới theo goal; pilot hiện chỉ chấp nhận exact scalar lookup không thay lịch.'),
    ('appliances.list','appliances.stop','appliance_id'): ('R_PHYSICAL_ACTION','READ danh sách an toàn nhưng consumer dừng thiết bị vật lý; cần review room/type/state và live ToolEnv behavior riêng.'),
    ('banking.get_balance','banking.list_transactions','account_id'): ('R_FINANCIAL_READ_SCOPE','Optional account filter có thể resolve theo tên, nhưng balance/transactions là dữ liệu tài chính; chưa review account intent và unnecessary balance READ trên đường có sẵn account ID.'),
    ('banking.get_balance','banking.open_savings','from_account_id'): ('R_FINANCIAL_WRITE','Chỉ ID tài khoản không đủ chứng minh goal-scoped amount/term và tài khoản nguồn; loại khỏi pilot READ-only consumers.'),
    ('banking.get_balance','banking.transfer','from_account_id'): ('R_FINANCIAL_WRITE','Cần account nguồn, beneficiary và amount cùng goal; không phê duyệt riêng source-account lookup trước khi kiểm all-field provenance.'),
    ('banking.list_beneficiaries','banking.transfer','beneficiary_id'): ('R_FINANCIAL_RECIPIENT','Cần đối chiếu beneficiary name/account/bank và amount cùng mục tiêu; exact value-flow không đủ để cấp quyền cho transfer.'),
    ('bike_rental.find_bikes','bike_rental.reserve','bike_id'): ('R_RESOURCE_STATE','Cần available=true, đúng station, giá/ý định thuê; matcher unique identity chưa phải availability predicate.'),
    ('calendar.list_events','calendar.cancel_event','event_id'): ('R_TEMPORAL_DESTRUCTIVE','Title có thể lặp nhiều ngày; chưa review date/attendee/occurrence và yêu cầu hủy tương ứng.'),
    ('calendar.list_events','calendar.update_event','event_id'): ('R_TEMPORAL_EDIT','Phải giữ đúng event occurrence và derived start/end/condition; current exact selectors không tự xử lý relative dates.'),
    ('chat_ott.list_chats','chat_ott.call','chat_id'): ('R_EXTERNAL_COMMUNICATION','Gọi thoại/video có tác dụng bên ngoài; cần review chat/group identity và action intent ngoài lookup ID.'),
    ('chat_ott.list_chats','chat_ott.send_location','chat_id'): ('R_LOCATION_DISCLOSURE','Gửi vị trí cần review rõ đối tượng nhận và mục đích chia sẻ; READ tên chat chưa đủ cấp quyền cho luồng này.'),
    ('chat_ott.list_chats','chat_ott.send_message','chat_id'): ('R_EXTERNAL_COMMUNICATION','Cần message body đúng goal và đúng chat/group; các guarded recipient rules hiện không bao phủ OTT như ewallet.'),
    ('contacts.search','contacts.update','contact_id'): ('R_CONTACT_MUTATION','Phone/email là dữ liệu mới muốn sửa, không phải identity selector để tìm contact cũ; cần review x-one-of và old/new value semantics.'),
    ('contacts.search','ewallet.transfer','contact_id'): ('R_FINANCIAL_ALTERNATIVE','Có phone alternative; phải tránh preflight khi phone đã đủ và xác minh amount/recipient cùng clause. Giữ amount/recipient guards và chưa bật transfer pilot.'),
    ('contacts.search','phone.call','contact_id'): ('R_EXTERNAL_ALTERNATIVE','Phone_number có thể đã đủ; cần kiểm actual preflight với direct number và action gọi điện trước activation.'),
    ('contacts.search','sms.send','contact_id'): ('R_EXTERNAL_ALTERNATIVE','Phone_number alternative có thể bỏ lookup; cần body/recipient binding cùng goal và active-loop tests để tránh đọc dư hoặc gửi sai.'),
    ('door_lock.list_locks','door_lock.grant_guest_code','lock_id'): ('R_SECURITY_CRITICAL','Khóa cửa và tạo guest code cần security-specific authorization/valid_until review; loại khỏi pilot đầu, không coi producer READ là đủ.'),
    ('door_lock.list_locks','door_lock.set_lock','lock_id'): ('R_SECURITY_CRITICAL','Lock/unlock tác động an ninh; cần door/location/locked intent và action safety review riêng.'),
    ('email.list','email.archive','email_id'): ('R_DESTRUCTIVE_MESSAGE','Archive/delete cần đúng email occurrence/action; title duy nhất trong một response chưa đủ để duyệt xóa email.'),
    ('email.list','email.reply','email_id'): ('R_EXTERNAL_COMMUNICATION','Reply cần sender/thread/time và body cùng goal; chưa có generic outgoing-recipient guard cho email.'),
    ('flights.search','flights.book','flight_id'): ('R_TRAVEL_BOOKING','origin/destination/date phải có grounded producer bindings, không đoán tomorrow/IATA; còn fare/seat/passenger và financial booking semantics cần review.'),
    ('food_delivery.list_restaurants','ewallet.pay_qr','merchant_id'): ('R_MERCHANT_AMOUNT','Merchant_id khác restaurant id; path được quan sát nhưng không có exact-flow sample cho cạnh này; chưa review merchant/amount binding và live payment guards.'),
    ('home_automation.list_rules','home_automation.delete_rule','rule_id'): ('R_DESTRUCTIVE_AUTOMATION','Xóa rule cần device/action/time đúng mục tiêu; unique title không bảo đảm các qualifiers của rule.'),
    ('home_security.list_alerts','home_security.acknowledge_alert','alert_id'): ('R_SECURITY_ALERT','Cần đúng loại/vị trí/thời điểm alert và ý định acknowledged; không chọn bằng type/location đơn lẻ cho pilot.'),
    ('learning.list_courses','learning.enroll','course_id'): ('R_RESOURCE_COMMITMENT','Enrollment có thể liên quan phí/seats/enrolled state; chỉ course name chưa đủ cấp quyền cho luồng đăng ký.'),
    ('learning.list_courses','learning.schedule_lesson','course_id'): ('R_SCHEDULE_COMMITMENT','Cần course/time/enrollment và lịch học của đúng goal; derived time còn phụ thuộc Policy.'),
    ('medication.list','medication.log_taken','schedule_id'): ('R_MEDICATION_RECORD','Ghi đã uống thuốc cần đúng schedule/time/intent; chưa review medical record mutation trong active harness.'),
    ('medication.list','medication.remove','schedule_id'): ('R_MEDICATION_DELETION','Xóa nhắc thuốc là dữ liệu nhạy cảm; cần schedule/time identity và deletion scope riêng.'),
    ('news.headlines','news.save','article_id'): ('R_COMPETING_PRODUCER','Có route news.search hẹp hơn; đồng bật hai routes cùng title sẽ NEED_MODEL_DECISION. Headline source path có observations nhưng exact-flow=0.'),
    ('podcast_radio.search','podcast_radio.play','episode_id'): ('R_ALTERNATIVE_SHOW_SCOPE','Station alternative có thể đủ; episode title/show identity và branch phải rõ. Cần active-loop tests cho cả hai branches trước duyệt play.'),
    ('reminders.list','reminders.delete','reminder_id'): ('R_DESTRUCTIVE_TIME','Xóa reminder cần đúng content/time/repeat và intent, khác với chỉ đánh dấu complete.'),
    ('ride_hailing.get_ride','ride_hailing.cancel','ride_id'): ('R_RIDE_CANCELLATION','Tool trả current ride; phải xác minh pickup/dropoff/time/status có đúng ride user muốn hủy, không suy từ single wrapper record.'),
    ('shopping_list.get','shopping_list.remove','item_id'): ('R_DESTRUCTIVE_LIST','Xóa item cần scope/quantity/identity review; đợt đầu chỉ ưu tiên READ consumers, không xóa dữ liệu.'),
    ('smart_home.get_status','home_automation.create_rule','device_id'): ('R_FUTURE_PHYSICAL_ACTION','Tạo automation cần device class/action/time và ảnh hưởng tương lai; lookup ID riêng chưa đủ.'),
    ('smart_home.get_status','smart_home.scene_run','scene_id'): ('R_BULK_PHYSICAL_ACTION','Scene có thể đổi nhiều thiết bị; name exact không chứng minh toàn bộ effects phù hợp request.'),
    ('subscriptions.list','subscriptions.cancel','subscription_id'): ('R_FINANCIAL_CANCELLATION','Cần đúng subscription/cycle/account và intent hủy; loại khỏi pilot trước khi có independent action review.'),
    ('tv.guide','tv.record','program_id'): ('R_PROGRAM_OCCURRENCE','Program title có thể lặp theo kênh/ngày; cần channel/start/end occurrence và time derivation đúng request.'),
    ('video_meeting.list_meetings','video_meeting.join','meeting_id'): ('R_MEETING_OCCURRENCE','Tham gia cuộc họp có privacy/external effects; cần title/start/attendees đúng occurrence, không chỉ unique title.'),
}


def make_observation(path, selector, value='probe_target', rows=1, extra=None):
    parts = [p for p in path.replace('[]', '.[]').split('.') if p]
    record = {parts[-1]: value, selector: 'Probe Entity', **(extra or {})}
    current = record
    for part in reversed(parts[:-1]):
        if part == '[]':
            current = [copy.deepcopy(current) for _ in range(rows)]
        else:
            current = {part: current}
    current['ok'] = True
    return current


def binder_probe(edge, tools, selector, bindings):
    # A data-helper check, NOT resolver construction, plan generation or authorization.
    from types import SimpleNamespace
    helper = SimpleNamespace(tools=tools)
    pending = copy.deepcopy(edge)
    pending['producer_argument_bindings'] = bindings
    assert pending['operator_decision'] == 'PENDING' and pending['runtime_enabled'] is False
    request = 'Probe Entity'
    selectors = {selector: GroundedValue(request, 0, len(request))}
    goal = GoalContext('probe_goal', edge['consumer_tool'])
    args = {arg: request for arg in bindings}
    ledger = EvidenceLedger()
    def match():
        return BackwardDependencyResolver._match(helper, pending, selectors, ledger, goal)
    result = make_observation(edge['source_output_path'], selector)
    ledger.observe(edge['producer_tool'], args, result, goal.goal_id)
    positive = match()
    positive_status = positive[0] if positive else None
    ledger = EvidenceLedger()
    ledger.observe(edge['producer_tool'], args, result, 'other_goal')
    cross_goal_denied = match() is None
    ledger = EvidenceLedger()
    ledger.observe(edge['producer_tool'], args, result, goal.goal_id)
    ledger.observe('probe.write', {}, {'ok': True}, goal.goal_id, 'write')
    stale_denied = match() is None
    ledger = EvidenceLedger()
    ledger.observe(edge['producer_tool'], args, make_observation(edge['source_output_path'], selector, rows=2), goal.goal_id)
    duplicate = match()
    duplicate_abstains = duplicate is not None and duplicate[0] == 'AMBIGUOUS'
    ledger = EvidenceLedger()
    ledger.observe(edge['producer_tool'], args, {'ok': False}, goal.goal_id)
    failed_not_evidence = match() is None
    ledger = EvidenceLedger()
    ledger.observe(edge['producer_tool'], args, result, goal.goal_id)
    mismatch_selectors = {selector: GroundedValue('Other', 0, 5)}
    wrong_match = BackwardDependencyResolver._match(helper, pending, mismatch_selectors, ledger, goal)
    wrong_subject_denied = wrong_match is None or wrong_match[0] == 'MISMATCH'
    return {'method': 'PURE_MATCH_HELPER_NO_APPROVAL_NO_READ_PLAN_NO_EXECUTION',
            'unique_match_status': positive_status, 'duplicates_abstain': duplicate_abstains,
            'cross_goal_denied': cross_goal_denied, 'stale_denied': stale_denied,
            'failed_result_not_evidence': failed_not_evidence, 'wrong_subject_denied': wrong_subject_denied}


def special_guard_probes(root, graph):
    from lifemate_graph.b043 import load_b042
    m = load_b042(root/'inputs/LifeMate_B042_Progress_Retry_Controller_Kaggle.ipynb', '_b043_review_guards')
    item = {'audio_text': 'Thanh toán hóa đơn điện tháng này từ ví.', 'context': {}, 'history': []}
    ledger = m.EvidenceLedger(item)
    ledger.observe('bill_payment.list_bills', {'ok': True, 'bills': [{'id': 'probe_bill', 'type': 'electricity', 'amount': 123000}]}, 0, arguments={})
    problems, _ = m.additional_grounding_check('bill_payment.pay', {'bill_id': 'probe_bill', 'source': 'ewallet'},
                                               item, ledger, ['bill_payment.list_bills', 'bill_payment.pay'])
    # Probe proves the missing device-type predicate at matching level, never executes WRITE.
    from types import SimpleNamespace
    e = next(e for e in graph['dependencies'] if e['consumer_tool']=='smart_home.light_set' and e['validation_status']=='VERIFIED')
    dl = EvidenceLedger()
    dl.observe(e['producer_tool'], {}, make_observation(e['source_output_path'], 'name', extra={'type': 'fan'}), 'probe_goal')
    found = BackwardDependencyResolver._match(SimpleNamespace(tools=graph['tools']), e,
        {'name': GroundedValue('Probe Entity', 0, 12)}, dl, GoalContext('probe_goal', e['consumer_tool']))
    return {'bill_pay_schema_has_amount': 'amount' in graph['tools']['bill_payment.pay']['parameters']['properties'],
            'bill_pay_electric_request_guard_problems': problems,
            'light_set_matcher_accepts_fan_record_by_name': bool(found and found[0]=='OK'),
            'note': 'Read-only helper/guard probes; no ToolEnv, Qwen, resolver plan, graph compilation or WRITE execution.'}


def run(root=ROOT):
    root = Path(root).resolve()
    paths = ['artifacts/full_dependency_graph_v3.json', 'artifacts/graph_validation_report.json',
             'artifacts/graph_review_queue_v3.csv', 'artifacts/compiled_graph_v3.json', 'artifacts/graph_manifest.json',
             'GRAPH_V3_RUNTIME_INTEGRATION.md', 'approvals.example.json', 'lifemate_graph/compiler.py',
             'lifemate_graph/runtime.py', 'lifemate_graph/b043.py', 'lifemate_graph/cli.py', 'inputs/train_tools_catalog.json']
    before = {p: sha256(root/p) for p in paths}
    manifest = read_json(root/'artifacts/graph_manifest.json')
    for name, digest in manifest['artifacts_sha256'].items():
        if sha256(root/'artifacts'/name) != digest: raise ValueError('Existing artifact digest mismatch: '+name)
    graph = read_json(root/'artifacts/full_dependency_graph_v3.json')
    if object_sha(graph) != manifest['graph_content_sha256']: raise ValueError('Graph content hash mismatch')
    runtime = load_compiled(root/'artifacts/compiled_graph_v3.json', root/'artifacts/graph_manifest.json', root/'inputs/train_tools_catalog.json')
    if runtime['edges']: raise ValueError('This pre-approval review expects the untouched zero-grant baseline')
    for name, digest in manifest['implementation_sha256'].items():
        if sha256(root/'lifemate_graph'/name) != digest: raise ValueError('Implementation changed since graph manifest: '+name)
    verified = [e for e in graph['dependencies'] if e['validation_status']=='VERIFIED']
    keys = {(e['producer_tool'], e['consumer_tool'], e['target_argument_path']) for e in verified}
    reviewed = set(READY) | set(FIX) | set(REVIEW)
    if keys != reviewed: raise ValueError('Explicit per-edge reviews do not cover exact VERIFIED set: '+str(keys ^ reviewed))
    if set(READY)&set(FIX) or set(READY)&set(REVIEW) or set(FIX)&set(REVIEW): raise ValueError('Overlapping review decisions')
    old_review = {r['edge_id']: r for r in read_csv(root/'artifacts/graph_review_queue_v3.csv')}
    rows, probes, draft_decisions = [], {}, []
    for e in verified:
        key = e['producer_tool'], e['consumer_tool'], e['target_argument_path']
        pt, ct = graph['tools'][key[0]], graph['tools'][key[1]]
        target = next(f for f in fields(ct['parameters']) if f['path']==key[2])
        info = next(v for v in e['candidate_output_paths'] if v['path']==e['source_output_path'])
        assert pt['kind']=='read' and info['observed_types']
        assert old_review[e['edge_id']]['source_output_path']==e['source_output_path']
        if key in READY:
            selector, bindings, cost, reason = READY[key]
            status, issue = 'READY_FOR_PILOT', 'READY_SCALAR_EXACT_BINDING'
        else:
            status = 'NEEDS_FIX' if key in FIX else 'NEEDS_REVIEW'
            issue, reason = (FIX if key in FIX else REVIEW)[key]
            available = e['subject_binding_rule']['allowed_selector_keys']
            selector = next((s for s in ['name','title','subject','label','content','flight_id','id'] if s in available), 'id')
            bindings, cost = {}, 'UNREVIEWED'
        assert selector in e['subject_binding_rule']['allowed_selector_keys']
        p = binder_probe(e, graph['tools'], selector, bindings)
        if status=='READY_FOR_PILOT':
            assert p['unique_match_status']=='OK' and all(p[k] for k in ['duplicates_abstain','cross_goal_denied','stale_denied','failed_result_not_evidence','wrong_subject_denied'])
            producer_args = {arg: 'Probe Entity' for arg in bindings}
            assert not schema_errors(producer_args, pt['parameters']), key
        probes[e['edge_id']] = p
        evidence = {'contract_tier': e['contract_tier'], 'source_path_types': info['observed_types'],
            'typed_exact_flow_sample_count': info['exact_flow_samples'], 'source_record_keys': info['record_keys'],
            'source_files': sorted({s['file'] for s in e['evidence_sources']}),
            'original_evidence_sha256': object_sha(e['evidence_sources']),
            'code_references': ['lifemate_graph/runtime.py:_resolve/_match', 'lifemate_graph/b043.py:B043Bridge',
                                'lifemate_graph/compiler.py:compile_graph'], 'pure_binder_probe': p}
        conditions = {'existing_graph_required_when': e['required_when'],
            'runtime_gates': ['explicit current-request goal/selector spans', 'unique goal binding', 'condition=true',
                'producer and consumer individually available', 'unique exact record; no rank/first/fuzzy choice',
                'same goal_id and current write epoch', 'read depth/count/cost budget',
                'producer arguments from reviewed mapping or grounded goal bindings', 'original validators and ToolEnv.validate'],
            'operational_pilot_scope': 'Exact named entity only; request qualifiers must not be silently discarded. Relative/ordinal/semantic selection is outside this pilot.',
            'operational_scope_is_not_new_compiler_predicate': True}
        if key[1]=='sms.read_recent':
            conditions['optional_filter_intent'] = 'goal.required_optional contains contact_id OR Policy already proposed contact_id; unfiltered SMS request must not force lookup'
        activation = 'PROPOSE_PILOT_AWAITING_USER_APPROVAL' if key in PILOT else 'DEFER_READY_WRITE_CONSUMER' if status=='READY_FOR_PILOT' else 'DO_NOT_ACTIVATE'
        rows.append({'edge_id': e['edge_id'], 'producer_tool': key[0], 'consumer_tool': key[1],
            'source_output_path': e['source_output_path'], 'target_argument_path': key[2],
            'producer_kind': pt['kind'].upper(), 'consumer_kind': ct['kind'].upper(),
            'producer_description': pt['description'], 'consumer_description': ct['description'],
            'producer_required_arguments': pt['parameters'].get('required', []),
            'producer_argument_strategy': bindings if bindings else 'NO_ARGUMENTS_NEEDED' if not pt['parameters'].get('required') else 'REQUIRES_GROUNDED_GOAL_BINDINGS_OR_FIX',
            'target_type': target['type'], 'required_conditions': conditions,
            'alternative_branches': e['alternative_branches'],
            'subject_binding_strategy': {'strategy': e['selection_strategy'], 'reviewed_selector_keys': [selector], 'request_span_required': True},
            'risk_level': e['risk_level'], 'read_cost_proposal': cost, 'review_status': status,
            'issue_code': issue, 'evidence': evidence, 'reason': reason,
            'proposed_activation': activation, 'operator_decision': 'PENDING', 'runtime_enabled': False})
        if key in PILOT:
            assert ct['kind']=='read' and not e['alternative_branches'] and target['type']=='string'
            draft_decisions.append({'edge_id': e['edge_id'], 'decision': 'PENDING',
                'source_output_path': e['source_output_path'], 'selector_keys': [selector],
                'read_cost': cost, 'producer_argument_bindings': bindings,
                'reason': 'PROPOSED ONLY, awaiting user approval. '+reason})
    rows.sort(key=lambda r:(r['proposed_activation']!='PROPOSE_PILOT_AWAITING_USER_APPROVAL', r['review_status'], r['producer_tool'], r['consumer_tool']))
    draft_decisions.sort(key=lambda d:d['edge_id'])
    write_csv(root/'runtime_approval_review.csv', rows, list(rows[0]))
    draft = {'graph_content_sha256': manifest['graph_content_sha256'], 'reviewer': None, 'scope': 'PRODUCTION',
             'decisions': draft_decisions, 'proposal_status': 'AWAITING_USER_APPROVAL_NOT_AUTHORIZED',
             'catalog_sha256': graph['catalog_digest'], 'proposed_edge_count': len(draft_decisions),
             'instructions': 'All decisions remain PENDING and reviewer is deliberately null. Do not compile/activate this draft. After explicit user approval, create a separate operator_approvals.json containing only authorized edges, actual approval attribution, and APPROVED decisions.'}
    write_json(root/'operator_approvals.proposed.json', draft)
    special = special_guard_probes(root, graph)
    assert special['bill_pay_electric_request_guard_problems'] and not special['bill_pay_schema_has_amount']
    assert special['light_set_matcher_accepts_fan_record_by_name']
    after = {p: sha256(root/p) for p in paths}
    assert before == after
    summary = {'status': 'REVIEW_COMPLETE_AWAITING_USER_APPROVAL', 'review_date': '2026-11-15',
        'reviewer_role': 'Codex technical reviewer, NOT operator approver',
        'verified_edges_reviewed': len(rows), 'classification_counts': {s:sum(r['review_status']==s for r in rows)
            for s in ['READY_FOR_PILOT','NEEDS_FIX','NEEDS_REVIEW','NOT_ELIGIBLE']},
        'proposed_pilot_count': len(draft_decisions), 'proposed_consumers_are_all_read': True,
        'proposed_pilot_edge_ids': [d['edge_id'] for d in draft_decisions],
        'runtime_approved_edges_before': 0, 'runtime_approved_edges_after': len(runtime['edges']),
        'operator_approval_file_created': False, 'reviewed_runtime_graph_compiled': False,
        'source_sha256_unchanged': before, 'graph_content_sha256': manifest['graph_content_sha256'],
        'catalog_sha256': graph['catalog_digest'], 'pure_binder_probe_count': len(probes),
        'special_guard_probes': special,
        'gpu_smoke': {'status': 'USER_REPORTED_PASS', 'modes': ['GRAPH_DISABLED','GRAPH_SHADOW'],
                      'independently_rerun_this_review': False, 'active_graph_gpu_tested': False},
        'cli_capability': {'existing_commands': ['build','demo'], 'standalone_compile_existing_graph': False,
             'next_step_after_approval': 'Add a small compile CLI command around compile_graph(existing_graph, authorized_overlay); do not invoke builder/discovery.'},
        'limits': ['Pure binder probes are not reviewed-runtime closed-loop execution tests.',
            'READY means a bounded pilot is technically plausible; not production correctness certification.',
            'NFC/casefold exact matching preserves accents/roles; alias or relation requests may abstain.',
            'Current preflight sees empty proposed arguments; explicit supplied IDs must be represented in goal.bindings to suppress lookup.',
            'Policy-driven producer observations with no selected goal may be scoped None and cannot be reused by graph without a fix; test after authorization.',
            'Approval schema cannot enforce arbitrary semantic scope restrictions or new resource predicates.',
            'Five READ-consumer candidates were selected to avoid a new graph-assisted WRITE path in the first pilot.']}
    output = root/'runtime_review'; output.mkdir(exist_ok=True)
    write_json(output/'review_summary.json', summary)
    write_json(output/'binder_probes.json', {'authorization_granted': False, 'runtime_plan_or_tool_execution': False, 'edges': probes, 'special_guards': special})
    write_json(output/'review_manifest.json', {'inputs_sha256': before, 'graph_content_sha256': manifest['graph_content_sha256'],
        'review_script_sha256': sha256(Path(__file__)),
        'outputs_sha256': {n:sha256(root/n) for n in ['runtime_approval_review.csv','operator_approvals.proposed.json','runtime_review/review_summary.json','runtime_review/binder_probes.json']},
        'phase': 'PRE_APPROVAL', 'approvals_granted': 0})
    return summary


if __name__ == '__main__':
    summary = run()
    print(canonical({k:summary[k] for k in ['status','verified_edges_reviewed','classification_counts','proposed_pilot_count','runtime_approved_edges_after']}))
