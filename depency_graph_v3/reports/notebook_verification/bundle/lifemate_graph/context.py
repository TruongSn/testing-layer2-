"""Conservative context recovery from explicit text and actual READ observations.

This module has no dataset reader, sample IDs, model, executor or gold access.
Recovered values retain source spans; uncertain language goes back to Policy.
"""
from __future__ import annotations
import copy
import re
from datetime import datetime, timedelta
from .common import norm, field_equal, path_entries, fields, opaque_field
from .runtime import GoalContext, GroundedValue


def literal(text, value):
    if not isinstance(value, str) or not value: return None
    hits = list(re.finditer(r'(?<!\w)' + re.escape(value) + r'(?!\w)', text, re.I))
    if not hits: return None
    m = hits[-1]
    return GroundedValue(text[m.start():m.end()], m.start(), m.end())


def captured(text, pattern):
    ms = list(re.finditer(pattern, text, re.I))
    if len(ms) != 1: return None
    m = ms[0]; a, b = m.span(1)
    while a < b and text[a] in ' \"“': a += 1
    while b > a and text[b-1] in ' ,.;!?\"”': b -= 1
    return GroundedValue(text[a:b], a, b) if a < b else None


def corrected_text(text):
    """Only for the destination playlist slot; earlier subject clauses stay intact."""
    hits = list(re.finditer(r'\b(?:à không|à mà thôi|đổi thành)\b[, ]*', text, re.I))
    return hits[-1].end() if hits else 0


def address_variants(text,value):
    """A grammatical address prefix can be literal identity or an address term.
    Consider both spellings as a union; never choose one when both records exist.
    """
    variants={norm(value.value):value}
    prefix=re.search(r'\b(?:chị|anh|thằng|bạn|cô|chú)\s+$',text[:value.start],re.I)
    if prefix:
        full=GroundedValue(text[prefix.start():value.end],prefix.start(),value.end)
        variants[norm(full.value)]=full
    return variants


def explicit_targets(item, available):
    text = item.get('audio_text', '')
    # Conditional, negated and cancelled requests remain the existing Planner's job.
    if re.search(r'\b(nếu|đừng|không cần|hủy yêu cầu|huỷ yêu cầu)\b', text, re.I): return []
    patterns = {
        'chat_ott.read_chat': r'(?:mở|đọc|xem).*(?:chat|tin nhắn).*(?:Zalo|với)|(?:mở|đọc|xem).*Zalo',
        'music.add_to_playlist': r'(?:cho|thêm|bỏ).*(?:vào|playlist).*(?:nhạc|playlist)',
        'music.play': r'(?:phát|nghe|mở).*(?:bài|nhạc|playlist)',
        'food_delivery.place_order': r'đặt.*(?:quán|phần|suất)',
        'library.reserve': r'(?:mượn|giữ|đặt|lấy)\s+(?:giúp\s+\S+\s+)?cuốn\b',
    }
    found = [name for name, pattern in patterns.items() if name in available and re.search(pattern, text, re.I)]
    # A previous runtime ask can establish the pending action; the selection itself
    # must still come from the current utterance. Never import a previous tool result.
    if not found:
        for h in reversed(item.get('history', [])):
            if h.get('role') != 'assistant': continue
            for call in h.get('tool_calls', []):
                name = call.get('arguments', {}).get('tool_name') if call.get('name') == 'sys.ask_missing_info' else None
                if name in {'music.play','library.reserve'} and name in available and re.search(r'\b(?:bài|cuốn)\b', text, re.I): found.append(name)
            break
    return list(dict.fromkeys(found))


def supplement_goals(item, names, tracker):
    """Recover an omitted *intent*, never an executable WRITE proposal."""
    for name in explicit_targets(item, names):
        existing=[g for g in tracker.snapshot() if g['completion_tool']==name]
        if len(existing)==1:existing[0]['explicit_intent_proven']=True
        if not existing:
            tracker.goals.append({'goal_id':'explicit_' + name, 'completion_tool':name,
                'description':item.get('audio_text',''), 'conditional':False, 'status':'pending',
                'observed_step':None, 'source':'explicit_request_intent','explicit_intent_proven':True})


def clause_span(raw_goal, request, names):
    """Scope recovered multi-goal context only to one independently explicit clause."""
    matches=[]
    for match in re.finditer(r'[^;\n]+',request):
        clause=match.group()
        targets=explicit_targets({'audio_text':clause},names)
        description=raw_goal.get('description','').strip(' .!?')
        if raw_goal['completion_tool'] in targets or (description and norm(description)==norm(clause.strip(' .!?'))):
            matches.append(match.span())
    return matches[0] if len(matches)==1 and len(list(re.finditer(r'[^;\n]+',request)))>1 else None


def recover_context(raw_goal, request, graph, ledger, item, existing=None):
    tool = raw_goal['completion_tool']
    if tool not in graph['consumer_index']: return existing
    if existing is None and raw_goal.get('graph_context'): return None  # malformed extras are not authority
    if existing is None and len([g for g in item.get('_pending_goals',[]) if g['completion_tool']==tool]) > 1: return None
    goal = copy.deepcopy(existing) if existing else GoalContext(raw_goal['goal_id'], tool)
    if raw_goal.get('conditional') and existing is None: goal.condition = 'unknown'
    scoped = request[slice(*goal.request_span)] if goal.request_span else request
    offset = goal.request_span[0] if goal.request_span else 0
    if re.search(r'\b(?:nếu|chỉ khi|khi nào)\b',scoped,re.I) and not raw_goal.get('conditional'):
        goal.condition='unknown'  # a missing Planner flag cannot certify a condition

    def put(target, path, key, value, replace=False):
        if not value: return
        if offset and value.observation_sequence is None:
            value = GroundedValue(value.value, value.start+offset, value.end+offset)
        slot = target + ':' + path
        if replace or not goal.subjects.get(slot, goal.subjects.get(path)):
            goal.subjects[slot] = {key:value}

    if tool in {'phone.call','sms.send'}:
        phones=list(re.finditer(r'(?<!\d)(?:\+84|0)\d{8,10}(?!\d)',scoped))
        if len(phones)==1:
            m=phones[0]
            goal.bindings.setdefault(tool,{})['phone_number']=GroundedValue(m.group(),m.start()+offset,m.end()+offset)
        person=captured(scoped,r'\b(?:gọi|nhắn|gửi)(?:\s+(?:điện|tin|SMS))?\s+cho\s+(?:(?:chị|anh|bạn)\s+)?([^,.;:!?]+?)(?=\s+(?:theo số|số|với nội dung)\b|[,.;:!?]|$)')
        if person:
            variants=address_variants(scoped,person);matches=[]
            for o in ledger.current('contacts.search',goal.goal_id,include_unscoped=True)[-1:]:
                if isinstance(o.result,dict) and o.result.get('ok') is True:
                    matches=[r for r in o.result.get('contacts',[]) if norm(r.get('name','')) in variants]
            if len(matches)>1:goal.context_requirements['identity_ambiguity']={'status':'ambiguous','reason':'Recipient address variants are ambiguous'}
            elif len(matches)==1:person=variants[norm(matches[0]['name'])]
        put(tool,'contact_id','name',person)

    # Explicit noun phrases provide the first READ's selector. No arbitrary fallback query.
    if tool == 'chat_ott.read_chat':
        v = captured(scoped, r'(?:chat|Zalo).*?\bvới\s+(?:(?:chị|anh|thằng|bạn)\s+)?([^,.;!?]+?)(?=,|\s+em muốn|\s+để\b|[.!?]|$)')
        if v:
            variants=address_variants(scoped,v);matches=[]
            for o in ledger.current('chat_ott.list_chats',goal.goal_id,include_unscoped=True)[-1:]:
                if isinstance(o.result,dict) and o.result.get('ok') is True:
                    matches=[r for r in o.result.get('chats',[]) if norm(r.get('name','')) in variants]
            if len(matches)>1:goal.context_requirements['identity_ambiguity']={'status':'ambiguous','reason':'Address-prefix and literal contact spellings match multiple records'}
            elif len(matches)==1:v=variants[norm(matches[0]['name'])]
        put(tool, 'chat_id', 'name', v)
    if tool in {'library.reserve','music.play'}:
        key, marker, path = ('title','cuốn','book_id') if tool == 'library.reserve' else ('title','bài','track_id')
        v = captured(scoped, rf'\b{marker}\s+(.+?)(?=\s+đi\s+(?:ạ|em|nhé)|\s+đi[.!?]|[.!?]|$)')
        if re.search(r'\b(?:hoặc|hay là)\b', scoped, re.I): v=None
        put(tool, path, key, v)
        if v and tool == 'music.play' and not goal.selectors(tool,'playlist_id'):
            goal.selected_alternatives.setdefault(tool,['track_id'])
    if tool in {'food_delivery.place_order','food_delivery.get_menu'}:
        restaurant = captured(scoped, r'\bquán\s+([^,.;!?]+)')
        put(tool,'restaurant_id','name',restaurant)
        put('food_delivery.get_menu','restaurant_id','name',restaurant)
        dish = captured(scoped, r'\bđặt\s+(.+?)\s+ở\s+quán\b')
        if dish and tool == 'food_delivery.place_order': put(tool,'items[0].item_id','name',dish)
        group=captured(scoped,r'\bnhóm\s+chat\s+(.+?)\s+có\s+(?:mấy|bao nhiêu)\s+người')
        if group and tool=='food_delivery.place_order':
            req={'status':'unresolved','reason':'Quantity depends on the explicitly named chat group'}
            goal.context_requirements['chat_group_quantity']=req
            observations=ledger.current('chat_ott.list_chats',goal.goal_id)
            if observations:
                o=observations[-1]
                records=[r for r in o.result.get('chats',[]) if field_equal('name',r.get('name'),group.value)] if isinstance(o.result,dict) and o.result.get('ok') is True else []
                if len(records)>1:req.update(status='ambiguous',reason='Duplicate group names')
                elif len(records)==1:
                    members=records[0].get('members')
                    if records[0].get('type')!='group' or not isinstance(members,list) or not members or any(not isinstance(x,str) for x in members) or len(set(map(norm,members)))!=len(members):
                        req.update(status='ambiguous',reason='Group membership is empty, duplicated, or not countable')
                    elif dish and re.search(r'đúng\s+mấy\s+phần',scoped,re.I):
                        count=len(members)
                        profile=item.get('context',{}).get('user_profile',{}).get('name','')
                        if re.search(r'không\s+tính\s+mình',scoped,re.I):
                            own=[x for x in members if norm(x) in {'me','self',norm(profile)}]
                            # A shortened profile name could be the user: do not guess.
                            uncertain=[x for x in members if norm(x)!=norm(profile) and norm(x) not in {'me','self'} and norm(x)==norm(profile).split(' ',1)[-1]]
                            if uncertain:req.update(status='ambiguous',reason='Cannot prove whether a shortened member name denotes the user')
                            else:count-=len(own)
                        if req['status']!='ambiguous' and count>0:
                            req.update(status='satisfied',observation_sequence=o.sequence,
                                expected_arguments={'items[0].quantity':count},reason='Count of unique observed group members, excluding explicit self only')
                    else:req.update(status='unsupported',reason='Multiple dishes or unsupported quantity rule')

    # Catalog entity nouns, not dataset cases. Quotes and explicit field labels
    # are supported across tools; unrecognized/relative identity remains Policy's job.
    nouns={
        'contact_id':('name',r'liên hệ(?:\s+(?:tên|của))?|contact'),
        'chat_id':('name',r'nhóm chat|cuộc trò chuyện(?:\s+với)?'),
        'task_id':('title',r'nhiệm vụ(?:\s+tên)?|công việc(?:\s+tên)?'),
        'note_id':('title',r'ghi chú(?:\s+tên)?|note'),
        'course_id':('name',r'khóa học(?:\s+tên)?|khoá học(?:\s+tên)?'),
        'book_id':('title',r'cuốn|sách(?:\s+tên)?'),
        'article_id':('title',r'bài báo(?:\s+tên)?'),
        'email_id':('subject',r'(?:email|thư)\s+(?:có\s+)?tiêu đề'),
        'reminder_id':('content',r'lời nhắc(?:\s+về)?|nhắc nhở(?:\s+về)?'),
        'place_id':('name',r'địa điểm(?:\s+tên)?'),
        'scene_id':('name',r'ngữ cảnh(?:\s+tên)?|scene'),
        'track_id':('title',r'bài(?:\s+hát)?'),
        'playlist_id':('name',r'playlist(?:\s+tên)?|danh sách phát(?:\s+tên)?'),
        'episode_id':('title',r'tập podcast(?:\s+tên)?'),
        'show_id':('name',r'kênh podcast(?:\s+tên)?'),
        'device_id':('name',r'thiết bị(?:\s+tên)?'),
        'program_id':('title',r'chương trình(?:\s+tên)?'),
        'meeting_id':('title',r'cuộc họp(?:\s+tên)?'),
    }
    for edge_id in graph['consumer_index'].get(tool,[]):
        edge=graph['edges'][edge_id]
        if edge.get('context_requirement'):continue
        path=edge['target_argument_path'];entity=nouns.get(path)
        if path in {'to[]','attendees[]'}:entity=('name',r'(?:cho|với)')
        if entity:
            key,noun=entity
            value=captured(scoped,rf'\b(?:{noun})\s+[\"“]([^\"”]+)[\"”]')
            if value is None and path not in {'to[]','attendees[]'}:
                value=captured(scoped,rf'\b(?:{noun})\s+([^,.;!?\"”]+?)(?=\s+(?:nhé|đi em|đi ạ|giúp tôi|giúp em|hoàn thành|vào playlist|vào danh sách)(?:[.!?]|\b)|[,.;!?]|$)')
                if value and re.match(r'^(?:và|rồi|để|có|không|cần|nào|đó|này|khác|với)\b',value.value,re.I):value=None
            put(tool,path.replace('[]','[0]'),key,value)
        # Exact literal selector metadata is useful for dates, occurrence, room,
        # flight identity, and other compound selectors. No date/name conversion.
        slot=tool+':'+path.replace('[]','[0]')
        for key in edge['subject_binding_rule'].get('required_selector_keys',[]):
            value=captured(scoped,rf'(?<!\w){re.escape(key)}\s*[:=]\s*[\"“]([^\"”]+)[\"”]')
            if value:
                scoped_value=GroundedValue(value.value,value.start+offset,value.end+offset)
                goal.subjects.setdefault(slot,{})[key]=scoped_value

    # Exact observed labels may recover user spans, including case/Unicode only.
    # Never identify by list order, partial string, fuzzy name, or an existing ID alone.
    for id_ in graph['consumer_index'].get(tool, []):
        edge = graph['edges'][id_]
        if edge.get('context_requirement'): continue
        path = edge['target_argument_path']
        if '[]' in path: continue  # rows need explicit per-index intent
        candidates = {}
        for o in ledger.current(edge['producer_tool'], goal.goal_id, include_unscoped=True)[-1:]:
            if not isinstance(o.result,dict) or o.result.get('ok') is not True: continue
            for _, record in path_entries(o.result, edge['source_output_path']):
                for key in edge['subject_binding_rule']['allowed_selector_keys']:
                    if key not in {'name','title','subject','content','label'}: continue
                    value = record.get(key)
                    v = literal(scoped, value)
                    if not v: continue
                    if tool == 'music.add_to_playlist' and path == 'playlist_id' and v.start < corrected_text(scoped): continue
                    candidates[(key,norm(value))] = v
        if len(candidates) == 1:
            (key,_),v = next(iter(candidates.items()))
            put(tool,path,key,v)

    if tool == 'music.add_to_playlist':
        start = corrected_text(scoped)
        playlist = captured(scoped[start:], r'\b(nhạc\s+[^,.;!?]+?)(?=\s+(?:cơ|nhé|đi|ạ)(?:[.!?]|$)|[.!?]|$)')
        if playlist:
            playlist = GroundedValue(playlist.value,playlist.start+start,playlist.end+start)
            put(tool,'playlist_id','name',playlist,replace=bool(start))
        referenced = re.search(r'\b(?:gửi|gởi|chia sẻ)\s+(?:qua\s+)?Zalo\b', scoped,re.I)
        if referenced:
            sender = captured(scoped, r'\bbài\s+(?:(?:thằng|chị|anh|bạn|cô|chú)\s+)?([^,.;!?]+?)\s+(?:gửi|gởi|chia sẻ)\s+(?:qua\s+)?Zalo\b')
            put('chat_ott.read_chat','chat_id','name',sender)
            requirement = {'status':'unresolved','reason':'Referenced track requires the matching chat message'}
            goal.context_requirements['chat_referenced_track'] = requirement
            # Missing source sender/time is an abstention, never a guessed query.
            if sender:
                chats = []
                for o in ledger.current('chat_ott.list_chats',goal.goal_id,include_unscoped=True)[-1:]:
                    if isinstance(o.result,dict) and o.result.get('ok') is True:
                        variants=address_variants(scoped,sender)
                        chats = [r for r in o.result.get('chats',[]) if norm(r.get('name','')) in variants]
                        if len(chats)==1:
                            sender=variants[norm(chats[0]['name'])]
                            put('chat_ott.read_chat','chat_id','name',sender,replace=True)
                if len(chats)>1: requirement.update(status='ambiguous',reason='Duplicate referenced chat subjects')
                elif len(chats)==1:
                    matches=[]
                    for o in ledger.current('chat_ott.read_chat',goal.goal_id):
                        if o.arguments.get('chat_id')!=chats[0].get('id') or not isinstance(o.result,dict) or o.result.get('ok') is not True: continue
                        for message in o.result.get('messages',[]):
                            if not field_equal('name',message.get('from'),sender.value): continue
                            if re.search(r'\btối qua\b', scoped, re.I):
                                try:
                                    now=datetime.fromisoformat(item['context']['current_time'])
                                    stamp=datetime.fromisoformat(message['time'])
                                    if now.tzinfo is None or stamp.tzinfo is None: continue
                                    stamp=stamp.astimezone(now.tzinfo)
                                    if stamp.date()!=(now-timedelta(days=1)).date() or stamp.hour<18: continue
                                except (KeyError,TypeError,ValueError): continue
                            elif re.search(r'\b(?:hôm|tối|sáng|chiều|hồi|lúc|ngày)\b', scoped, re.I):
                                requirement.update(status='unsupported',reason='Unsupported chat occurrence qualifier');continue
                            content=message.get('content','')
                            title=captured(content,r'\bbài\s+(.+?)\s+của\s+[^,.;!?]+')
                            if title: matches.append(GroundedValue(title.value,title.start,title.end,o.sequence,'messages[].content',content))
                    if len(matches)==1:
                        goal.subjects[tool+':track_id']={'title':matches[0]}
                        requirement.update(status='satisfied',observation_sequence=matches[0].observation_sequence,
                                           reason='Unique sender/time-bound observed message title span')
                    elif len(matches)>1: requirement.update(status='ambiguous',reason='Multiple matching song messages')
    return goal if goal.subjects or goal.bindings or goal.context_requirements else None
