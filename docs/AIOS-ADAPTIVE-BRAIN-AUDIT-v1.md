# AIOS_ADAPTIVE_BRAIN_AUDIT_V1 — Semantic Audit Specification (A+ REFINED DRAFT)

**Dự án:** AIOS-Renew — Kernel Engineering  
**Repository:** `trung-via/AIOS-renew`  
**Ngày xây dựng A:** 2026-10-10  
**Ngày hợp nhất A+:** 2026-10-11  
**Original design audit read-only anchor:** `main` = `16d84f50be8a32d76db11a8b4b07442ee117abdd` (historical design provenance; not current engineering state)  
**Human-approved planning proposal base:** `main` = `c205acbfb76ed843580d6d97c9d1309a095a5e52` (2026-10-12; recheck before merge)  
**Prospective status upon lawful merge:** **HUMAN_APPROVED_PLANNING_BASELINE / IMPLEMENTATION_PENDING / PROFILE_NOT_ACTIVE / PRODUCTION_VALIDATION_NOT_PROVED**. This document alone does not authorize an implementation RUN, activate a profile or establish verification truth.  
**Contract name (đề xuất):** `AIOS_ADAPTIVE_BRAIN_AUDIT_V1`  
**Proposed profile id (chưa tồn tại trong registry):** `aios-adaptive-brain-audit-v1`; `version: 1`  
**Tiền nhiệm:** `brain-high-value-v3` trong `.ai/brain-audit-profiles.yaml`  
**Nguồn hợp nhất:** bản A ngày 10/10 + independent reconstruction/comparison ngày 11/10. Bản A+ kế thừa phần nguyên giá trị và nhập các findings F01–F04; không tạo một versioned profile trong canonical registry ở bước soạn thảo.  
**Áp dụng:** Brain semantic decisions; không sửa hoặc thay thế Reviewer semantic review, Runtime verification hay Publisher.

> **Đề xuất cốt lõi (chưa đo hiệu quả):** Một thủ tục semantic audit, **hai giai đoạn**, **tám lens nền tảng luôn bắt buộc**, nhưng có **trọng tâm phản biện thích ứng với đối tượng quyết định và tác động kiến trúc**. Audit chương trình BO/VP đánh giá sự cần thiết, các phương án và hiệu quả North Star; audit TASK đánh giá tính đúng, phạm vi và tương thích; audit correction tập trung exact finding/delta và bảo toàn chứng cứ. Không sinh thêm một bộ máy điều phối hay cơ sở dữ liệu riêng.

---

## 1. Mục tiêu, phạm vi và non-goals

### 1.1. Mục tiêu

1. Ngăn lặp lại mẫu **locally correct, globally unnecessary**: một chuỗi TASK có hợp đồng hợp lệ nhưng tổng kiến trúc tăng chi phí và độ phức tạp mà chưa chứng minh lợi ích.
2. Giữ chuẩn hiến định: đúng việc, đúng quyền, đủ bằng chứng, truy vết được, không lãng phí; tối ưu `Verified Useful Work / (Time + Tokens + Human Effort)`.
3. Giảm chi phí audit không cần thiết trên lỗi cục bộ, REPAIR/REMEDIATION và TASK delta nhỏ, nhưng không miễn nghĩa vụ kiểm tra hai vòng hiện hành.
4. Giữ việc giải nghĩa, phân tích trade-off và lựa chọn WHAT/WHY ở Brain/Human; dữ kiện từ Git, unified state và evidence vẫn thuộc nguồn canonical tương ứng.
5. Tái sử dụng nguồn đã được audit/đã công bố khi còn phù hợp, mở rộng context chỉ khi thay đổi tác động hoặc có ambiguity.
6. Tách **đúng hợp đồng triển khai** khỏi **đúng lựa chọn kiến trúc** và **đã đo được hiệu quả**.

### 1.2. Phạm vi

- **ARCHITECTURE** (BO/VP, Kernel/Constitution planning, security/threat-model change, verification architecture, cross-cutting governance).
- **TASK_AUTHORING** (new TASK và revision: feature, integration, technical hardening, rollout, recovery).
- **REPAIR_AUTHORING / REMEDIATION_AUTHORING** (exact correction lineage, Human-approved strategy/risk).
- Hỗ trợ **DIAGNOSTIC / RA research** ở dạng cung cấp dữ kiện và giả thuyết cho các quyết định trên; không mặc nhiên chuyển DIAGNOSTIC thành một two-stage authoring flow.

### 1.3. Cấm mở rộng (non-goals)

- Không sửa Constitution, Manifesto, frozen Kernel v0.1 hoặc KA-01 bằng cách diễn giải lại tài liệu audit.
- Không tạo Brain thứ hai, Planner, Audit Router, persistent reasoning ledger, semantic knowledge store, RAG/vector DB, new lifecycle authority, Runtime ROI oracle, tự động sửa/dispatch/publish, self-correction selector hoặc model chooser.
- Không thay thế canonical verification của Runtime bằng Brain claims, source web, RA report, test count, hay bảng đo hiệu suất.
- Không thay Reviewer semantic PASS / CHANGES_REQUIRED; Reviewer vẫn độc lập với Brain audit.
- Không tự động sửa roadmap, chuyển NEXT, kích hoạt một VP/BO, chọn Executor, tăng budget, nới scope hay chấp nhận rủi ro thay Human.
- Không yêu cầu serialize Decision Packet / Stage-1 / Stage-2 / audit ledger làm điều kiện AUTHOR_TASK mutation sau khi BO-1 đã loại bỏ.
- Không biến mọi bug fix thành architecture/RA study; không ép một research gate cho mọi ý tưởng.

## 2. Authority baseline — bắt buộc, không thỏa hiệp

| Authority | Quyền trong cơ chế mới | Không được làm |
|---|---|---|
| Human | Mục tiêu, ưu tiên, risk/threat acceptance, Executor delegation, constitutional/kernel change | Không bị thay bằng score hay automatic policy selector |
| Brain | Ý nghĩa WHAT/WHY, kiến trúc, lựa chọn alternatives và trade-off trong thẩm quyền, TASK/correction semantics, hai vòng audit | Không production code, không tự thăng quyền, không tự review/publish |
| Executor đã được admit (duy nhất) | HOW, sửa code, local iteration trong phạm vi được giao | Không tự xác nhận canonical PASS, đổi hợp đồng hoặc risk acceptance |
| AIOS Runtime | Identity, admission, lineage, deterministic verification, evidence, CAS, lifecycle reduction | Không suy luận lựa chọn kiến trúc, semantically select remediation/repair hay quyết định ROI |
| Reviewer | Independent semantic verdict trên exact candidate và canonical evidence | Không thay Brain architecture/human authority |
| Publisher | Publish đúng exact eligible reviewed source | Không quyết định giá trị kiến trúc hay tạo review mới |
| Transport / GitHub / RA / telemetry | Chuyển tải, quan sát, nghiên cứu và dữ liệu hỗ trợ | Không biến carrier accepted, raw PASS hoặc số liệu thành engineering truth |

**Tám lens V3 được duy trì nguyên nghĩa cơ bản: Stage 1 dùng chúng để định hướng construct; Stage 2 bắt buộc audit và final closure đầy đủ cả tám lens đối với flows đã thuộc phạm vi:** `AUTHORITY_BOUNDARY`, `SCOPE_NON_GOALS`, `PROVENANCE_LINEAGE`, `FAILURE_MODE_COUNTEREXAMPLES`, `AC_CONSISTENCY_COMPLETENESS`, `VERIFICATION_OWNERSHIP_ORDERING`, `PORTABILITY_PRIVACY_BOUNDEDNESS`, `SIMPLIFICATION_DUPLICATE_AUTHORITY`.

Mỗi lens có thể được giải trình rất ngắn nếu không có rủi ro, nhưng **không có quyền `NOT_APPLICABLE` để bỏ lens** hoặc rút yêu cầu closure hiện hữu. Hard constitutional/frozen-kernel/privacy/evidence floors luôn là điều kiện không thể được bù trừ bằng tốc độ.

## 3. Nguyên tắc thiết kế: một profile, các focus overlay không tạo authority

### 3.1. V1 thay V3 ở mặt thủ tục sử dụng hiện hành

- Chỉ có **một active Brain audit procedure** ở một thời điểm cho authoring flows được phê duyệt.
- `AIOS_ADAPTIVE_BRAIN_AUDIT_V1` là **contract/protocol name**; nếu triển khai registry, ID hợp lệ theo current validator regex nên dùng `aios-adaptive-brain-audit-v1`, không lấy tên uppercase để đặt vào `id` của YAML profile.
- **Hai stage vẫn giữ nguyên tên:** `CONSTRUCT` và `ADVERSARIAL_AUDIT_AND_RECONCILE`; kết quả cuối vẫn `CANDIDATE | NO_DECISION`; mandatory final closure sweep vẫn bắt buộc.
- **Không thêm trường focus-selector vào TASK, RUN, RESULT, AUTHOR_TASK ingress, roadmap-state, Unified State hoặc Decision Packet v1.**
- Focus overlays là **nội dung thủ tục semantic bounded** của duy nhất profile V1, không phải tập profile được model tự chuyển qua lại, không có routing API, không có score/xếp hạng tự động.
- **Normative focus semantics must be profile-bound:** Mọi nghĩa vụ phản biện theo F-A…F-H áp dụng trong phiên triển khai phải xuất phát từ normalized, digest-bound audit-profile material hoặc contract canonical được tham chiếu bằng một binding hợp lệ. Không để một note ngoài Stage-1 contract hoặc một prompt adapter tuỳ provider trở thành nguồn quyết định lens/focus độc lập. Ưu tiên tái sử dụng các trường `lenses[].check` và procedure hiện hữu trong bounds; nếu không thể biểu đạt đầy đủ trong closed profile format hiện hành, phải xem xét một thay đổi profile/protocol có version riêng, thay vì thêm field bí mật hay tự bỏ bớt bảo đảm.
- Brain xác định những boundary *chịu ảnh hưởng thực tế* từ Human intent + canonical exact subject/context. Một đối tượng có thể kích hoạt nhiều focus. Nếu chưa rõ, bao phủ hợp nhất những focus có khả năng liên quan hoặc kết luận `NO_DECISION` để lấy thêm dữ kiện đúng thẩm quyền; không cho phép chọn hẹp để tránh một nghĩa vụ.

### 3.2. Các focus bổ sung

| Focus | Khi material effect xuất hiện | Bổ sung câu hỏi bắt buộc bên cạnh 8 lens |
|---|---|---|
| **F-A ARCHITECTURE_NORTH_STAR** | BO/VP, thay thế/giới thiệu protocol, service, architecture, chuỗi prerequisite | Necessity; A/B/C alternatives; baseline; full-episode cost; dependency spiral; reversibility; benefit proof |
| **F-B CONSTITUTION_KERNEL** | Động đến constitutional authority, frozen Kernel, amendment, future versions | Human authority; prospective-only; legacy decode; non-retroactivity; exact semantic compatibility |
| **F-C PROOF_EVIDENCE** | Verification/evidence validity/reuse/checkpoint/acceptance | Exact subject; validity INVALIDATED/UNKNOWN; proof completeness; no false PASS; no duplicate lawful execution |
| **F-D SECURITY_TRUST** | Origin, identity, issuer, credential, role, secret, ruleset, external trust | Explicit threat actor, trust root, threat necessity, privilege isolation, replay/negative cases, live deployment reality and overhead |
| **F-E WORKFLOW_INTEGRATION** | Authoring/dispatch/wake/review/publication/coordination | End-to-end affected consumers; state transition; race/CAS; idempotence; transport != terminal; sticky delegation |
| **F-F CORRECTION_DEFECT** | Exact FAILURE/REVIEW finding, regression, bounded fix | Causal finding; correction lineage; smallest legal delta; valid-proof preservation; negative regression; stop if new intent |
| **F-G PERFORMANCE_EFFICIENCY** | Claim về time/token/test savings, infrastructure, test scheduling | Comparable baseline; whole episode; per-stage timing; test items; evidence reuse; Human ops; no false PASS |
| **F-H RECOVERY_RETIREMENT** | Retirement, forward recovery, migration, rollback | Baseline exact; preserve history; remove obsolete hooks; keep legitimate safety guards; rollback/compatibility; scoped conformance |

**Phân loại ban đầu theo subject, không theo tên TASK hoặc chỉ theo từ khóa.** Ví dụ một VP mới mang tên “lean” nhưng thêm issuer/service phải đánh giá F-A + F-D + F-C. Một TASK nhỏ sửa provenance issuer có thể kích hoạt F-D dù được gọi là “bug fix”. Độ lớn dòng code không đủ để xác định mức audit.

### 3.3. Ba lớp độ sâu (hướng dẫn, không phải loại authority)

- **Strategic:** BO/VP/Constitution/Kernel, thay đổi policy hoặc cross-cutting trust. Hai vòng sâu, so sánh phương án, North Star, RA nếu thật sự cần; đánh giá cả chương trình và các prerequisite.
- **Engineering:** new TASK/feature/integration/performance. Hai vòng task-specific và impact-map; vẫn kích hoạt F-A nếu TASK làm thay đổi kiến trúc nền tảng.
- **Correction:** REPAIR/REMEDIATION hoặc bounded regression. Hai vòng tập trung exact lineage, causal delta và evidence reuse; không dựng business case dài cho bug cục bộ.

**Không dùng automatic tier-score, threshold LOC hoặc model tự ghi “low risk” rồi bỏ nghĩa vụ.** Stage 2 phải phản biện tính đầy đủ của focus lựa chọn từ Stage 1.

## 4. Stage 1 — CONSTRUCT (bắt buộc)

### 4.1. Input tối thiểu đủ dùng

1. Current Human intent và decision authority (đọc đúng chỉ thị hiện hành).
2. Current canonical `main` + minimum-sufficient governance: Manifesto, Constitution, Project Contract; chỉ mở rộng frozen Kernel, active architecture, focused predecessor và downstream contract khi thực sự bị tác động.
3. Exact `subject`: planning objective hoặc TASK revision, RUN/FAILURE/REVIEW/candidate/lineage; không đoán từ chat.
4. Current selected Brain flow từ existing bounded context/Flow Card/Decision Packet; không dùng heuristic router thứ hai.
5. Valid existing evidence & performance observations khi quyết định phụ thuộc; provenance rõ, thiếu dữ kiện = `UNKNOWN`.
6. Optional bounded `research-high-value-v1` output nếu RA thực sự được dùng, với source/time/version/scope và uncertainty.

### 4.2. Các bước nhận thức

**S1.1 — Frame.** Viết Human problem/desired verified useful outcome, risk floor, không nhầm transport activation với engineering success.

**S1.2 — Canonical scope.** Xác định current authority, exact predecessor, applicable Kernel rule, current design/known deferred, impact map. Nêu rõ what is unchanged, what is affected và không lặp cả repository.

**S1.3 — Impact focus.** Từ hậu quả thực tế của đề xuất, nhận diện tất cả focus F-A...F-H có liên quan. Chỉ là nội dung lý giải trong Brain semantic work, không là một persisted selector hay control-plane state.

**S1.4 — Construct candidate.** Viết candidate semantic contract WHAT/WHY, scope, non-goals, constraints, atomic independently claimable AC; nếu ARCHITECTURE thì viết candidate architecture decision và các điều kiện exit.

**S1.5 — Minimal counterfactual** (bắt buộc khi F-A hoặc F-D dẫn tới cơ chế mới):

- **A = Reuse/no-change**: dùng hiện hữu chính xác được đến đâu?
- **B = Smallest bounded modification**: Runtime/Git CAS/verified evidence hiện có có đủ hard floors không?
- **C = New mechanism**: chỉ chọn khi có invariant/threat/functional requirement cụ thể không thể chứng minh bằng A/B.

Nếu A/B không an toàn, loại dựa trên *invariant được nêu rõ*, không loại vì sở thích hay hình thức; nếu Human đã chốt stronger threat model, Brain không được hạ thấp để tối ưu cost.

**S1.6 — Benefit/uncertainty.** Tách `OBSERVED` / `MEASURED` / `ESTIMATED` / `UNKNOWN` / `HYPOTHESIS`. Program-level phải nêu total-episode benefit model và cost of adoption, operation, migration, security, review, human intervention. Dữ liệu thiếu không tạo giả số ROI.

### 4.3. Stage 1 output

- Exact normalized `construct_candidate` theo shape của existing selected flow.
- `construct_fingerprint` gắn exact packet + normalized profile + construct candidate; **không bao gồm ghi chú bên ngoài Stage-1 contract**, và không mang provider/time/session.
- Transient bounded notes: useful outcome, focus giả định & lý do, observed constraints, candidate assumptions, alternatives (khi cần), known unknowns, initial risks. **Những notes nằm ngoài normalized candidate/packet chỉ là gợi ý, không authoritative, không tạo provenance/safety guarantee, không là input bắt buộc để Stage 2 đạt CLEAR và không vào final TASK schema hoặc ingress mutation gate.**
- Nếu một ràng buộc/threat/assumption có tính quyết định mà Stage 2 cần xem xét, đưa phần **semantic material được phép** vào construct candidate đã bound (nếu shape cho phép), hoặc bảo đảm nó có trong exact canonical packet/context tham chiếu hợp lệ; không nhúng audit envelope, CoT, focus selector hay private metadata vào candidate để lách schema. Nếu không đủ context đã bound, chuyển `NO_DECISION` hoặc làm lại Stage 1 trên packet đúng thẩm quyền.

## 5. Stage 2 — ADVERSARIAL_AUDIT_AND_RECONCILE (bắt buộc)

**S2.0 — Freshness:** Rebind đúng canonical subject, packet fingerprint, normalized profile ID/version/digest, Stage-1 construct SHA. Nếu exact dependency bị thay đổi, invalidation chính xác; quay lại Stage 1 theo protocol hiện hữu, không trộn hai candidate.

**S2.1 — Eight-lens challenge:** Audit đủ 8 lens V3 trên Stage-1 candidate, có bounded risks và counterexamples tương ứng.

**S2.2 — Independent focus-completeness attack (A+ F02):** Stage 2 **tự tái xác định toàn bộ material impacts** từ exact packet + normalized profile + Stage-1 bound candidate và những canonical context references còn hợp lệ. Không mặc nhiên tin danh sách focus/độ sâu mà Stage 1 từng nêu ở transient notes; một danh sách notes thiếu F-D không được miễn security audit nếu candidate thêm credential issuer. Đặt câu hỏi “có threat/proof/authority/verification/recovery effect nào bị gọi nhầm là local?” và thử các counterexample của những focus còn thiếu trong **cùng Stage 2**. Nếu phát hiện thiếu context mang tính quyết định hoặc altered subject/scope/profile/currentness, không suy diễn từ memory; fail closed (`NO_DECISION`) hoặc rebuild Stage 1 trên packet mới theo protocol hiện hữu. **Không có classifier score, focus registry, third stage hay new required schema field.**

**S2.3 — Counterfactual attack đặc biệt cho architecture/security:**

1. **Necessity attack:** Nếu bớt các module/adapter/key/service bổ sung, invariant/threat nào *cụ thể* không còn đảm bảo?
2. **Simpler implementation boundary:** Existing Runtime source/evidence, Git CAS, existing verifier có đáp ứng không, trong threat model đã chốt?
3. **Dependency spiral:** Mỗi prerequisite mới có đang giải khuyết tật do giả định vừa thêm? Có thể quay lại A/B thay vì tiếp tục phân rã C?
4. **Full-episode cost:** candidate PASS, REVIEW, publication, wake, setup, maintenance, test/replay, tool/Brain effort có được xem xét cùng nhau không?
5. **Safety/adoption:** Có hạ trust boundary, false PASS, leak raw data, phá Kernel v0.1, scope hay downstream pin không?

**S2.4 — Correction/performance attack:** Nếu sửa lỗi, yêu cầu causal finding, exact immutable lineage, evidence still valid, minimum legal delta. Nếu performance, chống claim giảm cost từ một test subset mà phá full valid obligations, hoặc chuyển chi phí sang Human/runner.

**S2.5 — Reconciliation:** Mỗi material risk phải `ADDRESSED_BY_RECONCILIATION` hoặc `DISMISSED_WITH_BOUNDED_BASIS`; không đơn giản tuyên bố CLEAR. Những rủi ro thuộc Human-owned risk/priority cần xin quyết định Human; những rủi ro thiếu canonical source cần `NO_DECISION` cho affected subject.

**S2.6 — Final closure sweep:** Chạy lại **đủ tám lens** trên reconciled candidate và thử lại counterexamples material theo focus, để không “fix A tạo lỗi B”. Kết quả `CANDIDATE` khi các lens đều CLEAR và các hard floor đạt; ngược lại `NO_DECISION`. Đây là Brain semantic judgment, không phải Reviewer PASS, Runtime RESULT hay roadmap READY.

## 6. Quy tắc North Star và chọn kiến trúc

### 6.0. Áp dụng North Star với **chính việc nâng V3 lên V1** (A+ F03)

Bản đặc tả này không mặc nhiên chứng minh rằng nâng profile đem lại giá trị. Trước khi Human chốt cutover, so sánh ba lựa chọn trên cùng các hard constitutional floors, không sử dụng nhãn “adaptive” như bằng chứng:

| Phương án | Thay đổi | Lợi ích có thể có | Chi phí / điểm yếu | Quyết định thiết kế |
|---|---|---|---|---|
| **A — Giữ V3** | Không đổi production profile; cải thiện Brain discipline/hướng dẫn ngoài đường production nếu hợp lệ | Ít rủi ro migration, không thêm parser/protocol changes | Khó ràng buộc bằng profile digest nếu adaptive guidance chỉ nằm trong chat/prompt; khả năng lặp lại qua provider chưa chứng minh | **Hợp lệ** nếu V1 không cho thêm useful findings đủ bù chi phí |
| **B — Successor tối thiểu** | Một profile V1 prospective, kế thừa 8 lens, two-stage, privacy/conformance guards; thêm bounded impact-specific semantic guidance | Có thể tăng khả năng phát hiện risk trong BO/VP mà vẫn hạn chế chi phí correction | Cần exact compatibility work, paired evaluation, cutover và historical support | **Preferred design hypothesis**, chưa phải measured winner |
| **C — Separate adaptive controller** | Focus router/classifier/secondary audit authority hoặc pipeline bên ngoài | Khó chứng minh lợi ích độc lập | Thêm authority, state, vận hành và trust surface | **Bác bỏ** theo Constitution/non-goals hiện hành |

Nếu A và B đều đáp ứng hard invariants, chỉ khuyến nghị B sau khi đối chiếu **incremental useful risk detection / cost** trên ca thật; nếu bằng chứng vẫn `UNKNOWN`, Human có thể chấp nhận pilot giới hạn hoặc tiếp tục V3. Không gán tự động `PASS`, không dùng số lượng tên lens/TASK/LOC hoặc bản thân profile version làm kết quả North Star. Việc từ chối C không phải lý do để tự động phê duyệt B.

### 6.1. Bắt buộc đối với program/architecture decision

Decision note phải trả lời **ngắn nhưng đủ**:

1. **Outcome:** Một kết quả verified useful nào phải tăng, hoặc rủi ro cụ thể nào Human buộc phải giảm?
2. **Baseline:** Dữ kiện current episode: wall time, tokens, Human interventions, test execution count, proof reuse, service/credential/ops dependency. Chưa đo = UNKNOWN.
3. **Alternatives:** A/B/C, mỗi phương án đáp ứng hoặc vi phạm hard invariant/threat requirement nào.
4. **Total cost:** Cả thiết lập ban đầu, maintain, migration, handoff, security overhead và retry/incident recovery; không chỉ source lines.
5. **Effectiveness evidence:** Sẽ chứng minh bằng comparable workloads/canonical evidence/BO-9B như thế nào; confidence và horizon được công khai.
6. **Re-evaluation:** Trigger nào khiến Brain phải rà lại kiến trúc; exit/retire/rollback nào được phép và Human có quyền quyết định gì.

### 6.2. Thứ tự so sánh không bù trừ

`Authority + Safety + Evidence Soundness + Human-approved constraints` → **pass tất cả hard floors** → mới so sánh vector `(verified outcomes, elapsed time, token usage, Human work, operational maintenance)`.

Không gộp time/tokens/Human thành weighted ROI chính tắc khi chưa có Human weights. Không đặt tỷ lệ lines-of-code, số TASK hoặc test speed làm hard policy gate. BO-9B chỉ quan sát, không chọn phương án.

### 6.3. Mức khẳng định

- `MEASURED_IMPROVEMENT` — chỉ khi baseline và candidate có evidence comparable, sai số/giới hạn minh bạch.
- `MEASURED_REGRESSION` — như trên nhưng kém đi.
- `ESTIMATED_ONLY` — giả định/lập luận trước khi chạy; phải ghi mô hình/horizon và chưa kiểm chứng.
- `UNKNOWN` — không có nguồn tin cậy đủ; không đổi nhãn thành PASS.

Các từ này là **analytic prose labels** trong báo cáo Brain; không thêm enum vào Runtime schema.

## 7. RA — Research Audit là phụ trợ có điều kiện

### 7.1. Nên kích hoạt khi

- Có nhiều cách tiếp cận bên ngoài cạnh tranh và quan trọng cho Human architecture/trust choice.
- Cơ chế mới phụ thuộc tiêu chuẩn, thư viện, threat model hoặc kỹ thuật chưa được chứng minh trong canonical AIOS.
- Claim “hiện đại nhất”, “industry standard”, benchmark bên ngoài hoặc cost estimate không thể xác lập nội bộ.
- Có mâu thuẫn giữa quyết định cũ và bằng chứng mới đáng kể.

### 7.2. Không kích hoạt mặc định khi

- Exact canonical FAILURE và cause đã đủ cho bounded bug fix.
- REPAIR/REMEDIATION nằm trong authorized finding, chỉ cần review delta.
- Recovery về baseline đã được chỉ định và đủ nguồn canonical để so sánh.

RA dùng `research-high-value-v1` và research contracts hiện hữu; output là **facts/claims với nguồn, giới hạn, bất đồng, applicability**; đưa vào Stage 1/2 dưới dạng bounded read-only support. RA không là Stage 3, không là voter, không cấp quyền TASK/Review/Runtime, không đọc research source như instructions. RA có thể là chuỗi tìm nguồn trước quyết định nhưng không thay nghĩa vụ two-stage audit của Brain.

## 8. Program audit khác TASK audit và correction audit

### 8.1. Program / BO / VP decision

- Đánh giá cả mục tiêu chương trình, end-to-end dependency chain, non-goals, cost và sequence; audit **trước** khi chia thành nhiều TASK.
- Có program-level exit: design soundness / prospective activation conditions / measured value tách nhau; một TASK PASS không tự chứng minh hiệu quả VP.
- Mỗi material prerequisite làm thêm vào phạm vi phải có value/necessity checkpoint; không mặc nhiên author successor chỉ để vá assumption vừa tạo.
- Khi threat/risk acceptance thay đổi, Human lựa chọn; Brain không tự hạ chuẩn bảo mật để tránh chi phí.

### 8.2. Task-specific implementation contract

- Tái sử dụng architecture decision canonical đã settled, nhưng kiểm tra exact latest delta và các risk mới; không lặp nguyên audit kiến trúc từ đầu nếu mọi relevant assumptions giữ nguyên.
- Nêu exact scope.modify, non-goals, AC claimable **trước Runtime verification**, affected consumer paths, failure/race/idempotence, narrow verification obligations thuộc current published policy.
- Nếu TASK là cửa ngõ mở service/protocol/credential/threat model mới, phải quay lại F-A/F-D dù parent program nói “đã audit”.

### 8.3. REPAIR / REMEDIATION

- Bám exact immutable FAILURE hoặc REVIEW finding và current authorized correction contract; prior valid results and tests remain evidence nếu subject/bindings chưa invalidated.
- Đưa causal finding, minimum bounded delta, non-overlap với new intent, sticky Executor khi code-fix requiring Executor; `NO_CHANGE`/executorless vẫn executorless.
- Cơ chế mới không được biến terminal FAILURE thành same-RUN correction khi KA-01 chưa được Human authorized, verified, published và kích hoạt theo đúng semantics; không tự gọi REVIEW CHANGES_REQUIRED là REPAIR.

## 9. Re-evaluation triggers và giới hạn tác động

**Các trigger định hướng Brain reasoning (không phải Runtime enforcement):**

- New always-on service, issuer, key, mTLS, trust root, broker, authentication roundtrip, credential writer role, source-of-truth adapter hoặc proof scheduler.
- Một prerequisite mới chỉ cần thiết để cứu một prerequisite trước đó, đặc biệt sau repeated structural failures.
- Mục tiêu Human ban đầu không đòi threat model mới nhưng giải pháp tự nâng lên mức bảo vệ cao hơn và sinh setup vận hành.
- Episode time/tokens/Human effort thực tế xấu đi hoặc chỉ số mục tiêu vẫn UNKNOWN trong khi định tuyên bố program exit.
- Schema/kernel compatibility hoặc immutable lineage gặp conflict; downstream pin/adoption chưa kiểm tra độc lập.
- Bằng chứng PASS được tái dùng qua modified subject/fixture/toolchain/environment mà không có witness hợp lệ.

**Khi trigger xảy ra:** Brain đọc minimum fresh relevant state, xem lại minimal alternatives + rủi ro, khuyến nghị tiếp tục/narrow/redesign/retire hoặc xin Human risk/priority; không tự sửa task/roadmap hoặc tạo RUN. Nếu current TASK scope không cho phép thay đổi, fail closed ở mutation boundary và chỉ đề xuất Human-authorized contract mới.

## 10. Tương thích kỹ thuật với V3 và chiến lược chuyển đổi

**V1 là mục tiêu đề xuất — không được đổi ID của V3 trong cùng bytes hay thay digest lặng lẽ.** Thay đổi profile content/format phải có SHA-256 chuẩn hóa mới, version mới và prospective contract.

### 10.1. Những compatibility seam thực tế từ canonical source

1. `.ai/brain-audit-profiles.yaml` hiện có `brain-high-value-v3` và `brain-high-value-v2`, registry format v1. Cần một published prospective representation cho V1; không ép ba profile vào parser hiện chỉ nhận tối đa hai profile.
2. `src/aios_renew/brain_audit.py` hiện `_profile(...)` chấp nhận cứng `brain-high-value-v2/v3`, đúng 8 lens ordered, closed profile fields và bounds, kiểm tra `TASK_AUTHORING` conformance của V3. Không thể đổi tên YAML rồi giả định V1 được admit.
   - **A+ F01 / critical migration seam:** `_normal(..., v3=False)` có logic loại các khóa `_V3_RESERVED_KEYS` chỉ khi `v3=True`; `_candidate(...)` kích hoạt bằng `policy["version"] == 3`. Profile successor mang `version: 1` sẽ **không tự động nhận được** bảo vệ V3 này nếu chỉ mở whitelist cho ID mới. Cần bảo toàn negative guards ngăn nested conformance/audit metadata, các private-key/path/other reserved controls, không chỉ tám lens. Đây là **nguy cơ regression nếu triển khai thiếu**, không phải một lỗ hổng V3 đã được chứng minh.
   - `validate_stage2(...)` cũng có nhánh `v3_task` cho `TASK_AUTHORING` conformance sections; migration phải duy trì tính chặt chẽ về `cross_authority_context`, `canonical_shape`, `terminal_lifecycle` và không hợp thức hóa `PROOF_LATER` làm completion claim.
3. `src/aios_renew/brain_provider_protocol.py` có nhánh theo `profile.version == 3` cho task conformance, và validation theo profile-specific fields. Khi V1 được kích hoạt, phải kiểm thử cả **request construction, raw provider response, validated stage outputs, exact profile reference và rejection paths**, bảo toàn `TASK_AUTHORING` conformance, acceptance-phase truthfulness, normalized reserved metadata/field exclusions và exact stage binding; không chuyển một điều kiện safety cũ thành optional chỉ vì version đổi.
4. `docs/AIOS-BRAIN-SEMANTIC-AUDIT-PROTOCOL.md`, `docs/CHATGPT_PROJECT_CONTRACT.md §11`, `docs/AIOS-RENEW-BRAIN-TASK-AUTHORING-CONTRACT.md` là những normative consumer cần đối chiếu; các đoạn lịch sử định danh V1/V2/V3 phải được đánh dấu đúng phạm vi thay vì thành policy cạnh tranh.
5. `tests/test_brain_audit.py` và các provider/conformance tests cần kiểm tra positive/negative cases cho V1 nhưng giữ legacy fixture/replay compatibility. Các consumer không được silently accept unknown profile IDs hoặc “fallback” về profile yếu hơn.

### 10.2. Quy tắc chuyển đổi tối thiểu

- **M0 (đặc tả):** Human duyệt nội dung V1, xác định phạm vi và priority; chưa triển khai.
- **M1 (separate TASK):** Brain author một TASK migration từ fresh main, scope đúng các consumer tương ứng; Executor triển khai HOW theo luật hiện hành; Runtime verify, Reviewer quyết định, Publisher publish. Chưa activate profile chỉ vì YAML tồn tại.
- **M2 (compatibility):** Kiểm tra V3 hiện hành và legacy subjects không bị reinterpret; Stage-1 V3 in-flight phải Stage-2 V3 cùng exact digest hoặc dừng/restart được authorise — tuyệt đối không Stage-1 V3 + Stage-2 V1. Có **negative parity cases** về reserved metadata keys, nested audit/conformance envelopes, machine-local/private paths, altered conformance sections và acceptance-phase truthfulness; V1 không được yếu hơn V3 cho cùng loại prospective subject.
- **M3 (single cutover):** Một current active profile V1 cho prospective flows, không chạy song song V3 và V1 để tạo hai verdict hoặc hai authoring ingress. Phần legacy support là decoding/replay compatibility, không decision authority thứ hai.
- **M4 (evaluation):** Trước claim superiority cần chạy paired V3–V1 trên **cùng preselected frozen canonical subjects** và cùng bounded material. Pilot V1 nếu được Human cho phép không đồng nghĩa đã đạt measured North Star; nếu thiếu comparable evidence ghi `UNKNOWN`, không tự thúc ép cutover hoặc rollback. Post-cutover telemetry, nếu có, là observability bổ sung chứ không độc lập quyết định giá trị.
- **M5 (cleanup):** Sau khi conformance publish và không còn active V3 authoring obligation, retire duplicated V3 user-facing instructions theo BO-10 governance hợp lệ; giữ historical immutable profile/lineage.

**Không đề xuất thay đổi schema `TASK`, `RUN`, `RESULT`, `FAILURE`, `REVIEW`, `AUTHOR_TASK`, `ROADMAP`, `Decision Packet v1` chỉ để lưu focus selection.** Nếu sau này một provider transport thật sự cần focus notes, ưu tiên truyền trong existing bounded semantic context; mọi extension protocol phải được xét riêng, exact versioned và non-authoritative.

## 11. Conformance — bắt buộc trước cutover

### 11.1. Structural and compatibility

- C01 — Đúng profile ID/version/digest mới; old V3/V2 được xử lý hợp lệ theo exact historical compatibility, không hạ audit. Cross-profile Stage 1/2, changed profile digest, old profile disguised under V1 id/version và unknown IDs đều fail closed; single prospective active profile được chứng minh.
- C02 — Hai stages duy nhất; Stage 1 fingerprint và Stage 2 binds giữ exact packet/profile/construct; stale/cross-subject/cross-profile fail closed.
- C03 — Tám lens ordered, mỗi lens được đánh giá; Stage 2 final closure đủ 8 lens; missing/reordered/duplicated lens bị reject.
- C04 — `TASK_AUTHORING` acceptance-phase, `cross_authority_context`, `canonical_shape`, `terminal_lifecycle`, shape/path sets và normalized reserved/private metadata guards của V3 còn hiệu lực trong V1; `PROOF_LATER` không thể trở thành Executor claim. Explicit negative tests với `_V3_RESERVED_KEYS`-equivalent và malformed provider/conformance sections bắt buộc.
- C05 — Không field mutable/new permission trong TASK/ingress; no new authoring audit transport gate; historical replay unchanged.
- C06 — Provider-neutral normalized profile bounded; no secrets, raw paths, CoT, chat memory, external prompt instruction nhập vào canonical authority. Adaptive semantics có identity/digest-bound profile basis; Stage 2 không dựa vào focus notes ngoài `construct_fingerprint` để xác nhận coverage.

### 11.2. Adaptive behavior

- C07 — BO/VP new architecture: A/B/C alternatives, North Star, total cost, threat model và measurable benefit questions bắt buộc; **A/B/C cũng được áp dụng cho chính lựa chọn giữ V3 hay chuyển V1**. Nếu phương án reuse V3 được chứng minh đủ, không mặc định đề nghị V1 chỉ vì version mới.
- C08 — Local H4 bug: two stages with causal delta and regression, không bị buộc mở full RA/three-option research nếu không có new architecture.
- C09 — Hybrid proof + security + workflow: union relevant focus, không mất một hard floor. Stage 2 phải phát hiện omitted F-D/F-A từ exact bound candidate dù Stage-1 transient focus notes chỉ ghi F-F, và không thêm `selected_focus` vào TASK/packet.
- C10 — Missing baseline: estimate/UNKNOWN, không claim measured saving; BO-9B metrics không ảnh hưởng mutation.
- C11 — New service/prerequisite xuất hiện trong Stage 2: reinterpret parent necessity; no silent next TASK/roadmap advancement.
- C12 — Mandatory Human-approved high-trust requirement: không được bypass để giảm cost; missing authority = `NO_DECISION`.
- C13 — Repair with exact failed subject: retain sticky Executor if required, preserve evidence unless invalidated, no new-intent laundering.
- C14 — Recovery: prospective forward recovery, immutable lineage, narrow safety hygiene; no reset/force-push/retroactive result.

### 11.3. Evaluation and exit

- C15 — Preselect một **paired evaluation set** có frozen SHA/context, tối thiểu TASK-320, TASK-329, TASK-334, TASK-336 và các bounded correction/ordinary feature cases ngoài VP03, được chọn **trước khi** xem output của hai profile; historical hindsight được khai báo, không gọi retrospective là real-time causal proof.
- C16 — V3 và V1 nhận cùng exact subject, Human intent, packet, relevant canonical references và evaluation questions; reviewer/adjudicator của bài evaluation phải phân biệt material/actionable finding, known missed risk, false positive, unnecessary work và limitations. Không sinh một canonical Reviewer/authority mới từ evaluator này.
- C17 — Không false PASS, không scope creep, không new authority, không semantic verdict auto-scripted; reviewer/publication riêng.
- C18 — Đo riêng wall time, provider input/output tokens (khi có nguồn), Human review minutes/interventions và useful material risks; không bắt RA cho local correction và không dùng tổng số finding hoặc token như một weighted utility score. Missing/unpaired source = `UNKNOWN`.
- C19 — Exact canonical source movement trong Stage 1/2 gây invalidate/restart như protocol hiện có.
- C20 — Prospective activation chỉ sau implementation TASK được Runtime verified, Reviewer verdict và Publisher exact-source publication hợp lệ, cùng explicit Human planning reconciliation/cutover. DRAFT, self-audit, paired desk-check hay test-only results đều không tự activate V1 hoặc tạo TASK/roadmap mutation.

### 11.3A. Paired-evaluation protocol để kiểm tra giá trị tăng thêm (A+ F04)

Phương pháp này là **điều kiện cho claim V1 better than V3**, không phải điều kiện để Human suy xét thiết kế trên giấy. Không tạo Reviewer thứ hai, scorer, trusted oracle, recurring benchmark service hay mandatory lifecycle gate.

1. **Pre-registration / dataset:** Chốt một tập ca có tên, canonical SHA, exact TASK/RUN/REVIEW evidence (nếu dùng), Human intent hợp lệ, known/unknown risks và tiêu chí so sánh trước khi xem audit output; không chọn chỉ các ca VP-03 nơi V1 được nghĩ ra nhờ hindsight. Bao phủ strategic, proof/security, task integration, correction và recovery; nếu dùng retrospective thì khai báo hindsight leakage.
2. **Input parity:** Cả V3 lẫn V1 được cung cấp cùng các dữ kiện verified/canonical và cùng câu hỏi; difference được phép chỉ là semantic profile/rules của hai phương án. Chạy đúng hai stages cho mỗi profile, với exact profile binding riêng, không ghép stage khác profile. Nếu model/provider/effort/order khác biệt không kiểm soát được, báo confound; không gán khác biệt cho audit profile một cách chắc chắn.
3. **Observed records:** Lưu bounded read-only evaluation notes về actionable finding, false positive, missed known finding, unjustified scope/authority expansion, prevented concrete architecture dependency, cost/token/wall time/Human minutes và provenance của nguồn đo. Không đưa các notes này vào Runtime state hoặc trở thành mutation-proof.
4. **Adjudication:** Human/Brain đối chiếu material risk với evidence và declared criteria; không lấy số lượng finding làm thước đo chất lượng. Với tranh chấp chưa đủ evidence, kết quả `UNRESOLVED/UNKNOWN`; evaluator không phát hành canonical REVIEW PASS.
5. **Decision:** Nếu V1 cải thiện useful decisions trong phạm vi cost Human chấp nhận và không regression trên hard guards, Brain khuyến nghị B; nếu lợi ích không material hoặc chi phí tăng, A (giữ V3) tiếp tục hợp lệ. Human chốt priority, risk/cost trade-off và adoption. **Không định nghĩa automatic numeric pass threshold hoặc weighted score.**

**Phân biệt nhãn kết quả:** `DESK_POLICY_COVERED` ≠ `IMPLEMENTATION_CONFORMANCE_PASS` ≠ `MEASURED_DECISION_QUALITY_IMPROVEMENT` ≠ `HUMAN_APPROVED_PROSPECTIVE_CUTOVER`. Trước evidence hữu hiệu, North-Star superiority phải ghi `UNKNOWN`.

### 11.4. 30-TASK retrospective classification (primary focus)

Mẫu là **30 canonical TASK records có trong main** theo thứ tự ID, không được coi là 30 RUN thành công hoặc cặp A/B measured experiment. Classification là primary focus; một TASK có thể sử dụng thêm focus khác.

| TASK | Ghi nhận mục tiêu chính | Primary focus | Focus phối hợp trọng yếu |
|---|---|---|---|
| 302 | H4D unattended wake acquisition | F-E | F-D |
| 303 | Protocol/cleanup correction | F-F | F-E |
| 304 | Browser listener pre-launch correction | F-F | F-E |
| 305 | Dedicated profile ownership correction | F-F | F-D |
| 306 | Browser context acquisition correction | F-F | F-E |
| 307 | Chromium launch marker cleanup | F-F | F-D |
| 308 | Async publication handoff boundary | F-E | F-G |
| 309 | Origin provenance binding | F-D | F-E |
| 313 | AUTHOR_TASK direct-final ingress | F-E | F-A |
| 314 | Reusable structural ResultPackage | F-E | F-C |
| 315 | Executorless finalize continuation | F-E | F-F |
| 316 | Verification baseline fixture recovery | F-F | F-C |
| 317 | V2 verification architecture | F-A | F-C, F-G |
| 318 | Self-host end-to-end normative flow | F-E | F-A |
| 319 | Full suite evidence + cleanup reliability | F-G | F-C |
| 320 | V2 redundant pytest execution | F-G | F-C |
| 321 | Canonical Context Pipeline | F-A | F-G |
| 322 | Correction-authoring subject resolution | F-E | F-F |
| 325 | Origin admission secret propagation | F-F | F-D |
| 326 | VP-02 proof contract / mapping | F-A | F-C |
| 327 | Publication end-to-end recovery | F-E | F-H |
| 328 | VP-02B intra-run correction proposal | F-A | F-B, F-C |
| 329 | Kernel KA-01 prospective amendment | F-B | F-A |
| 330 | VP-03A evidence applicability | F-C | F-A |
| 331 | VP-03B checkpoint lineage | F-C | F-A |
| 332 | VP-03C source handoff | F-C | F-D, F-A |
| 333 | VP-03D Runtime issuer bridge | F-D | F-C, F-A |
| 334 | Reviewer issuance + writer trust | F-D | F-A, F-E |
| 335 | Owner-bound provenance bridge | F-D | F-A, F-E |
| 336 | Pre-VP03 forward recovery | F-H | F-E, F-C |

**Retrospective limitation:** Bảng trên đọc goal/problem và một số contract tương ứng, không chứng minh nguyên nhân mọi failure, không thay exact individual lineage, không thể dùng làm benchmark token/time.

## 12. Audit lịch sử của A và final closure trên bản hợp nhất A+

### 12.1. Audit ban đầu của A — Vòng 1 CONSTRUCT (historical draft findings)

- **AA-01 / Critical:** Nếu thêm tám “profiles” có quyền riêng, sẽ tạo chính cơ chế routing/bureaucracy trái North Star. **Giải:** Một normalized active profile; overlays chỉ là semantic focus trong lens text, không có runtime selector.
- **AA-02 / Critical:** Adaptive classification có thể được Brain lợi dụng vô ý để bỏ lens khó. **Giải:** Tám lens bắt buộc không được N/A; Stage 2 adversarial coverage/union khi có tác động hỗn hợp.
- **AA-03 / High:** Hardcoded V3 profile/version trong implementation làm rename fail hoặc mất TASK authoring conformance. **Giải:** Separate implementation TASK, exact digest binding và compatibility conformance, không replace YAML đơn thuần.
- **AA-04 / High:** RA có nguy cơ biến thành third audit stage hoặc external information authority. **Giải:** RA được giới hạn thành nguồn read-only trước hoặc trong two-stage reasoning, giữ source uncertainty, không cấp quyền.
- **AA-05 / High:** Architecture cost lens có nguy cơ trở thành numeric score tự quyết thay Human. **Giải:** Hard floors trước, sau đó multi-axis comparison và recommendation; UNKNOWN ở dữ liệu thiếu; Human quyết tradeoff.
- **AA-06 / Medium:** Quá nặng cho correction nhỏ. **Giải:** bounded delta review trong cùng hai stages; không bắt làm generic A/B/C/RA cho cases cục bộ.
- **AA-07 / High:** V3-stage1 sang V1-stage2 sau cutover có thể nhầm subject/profile. **Giải:** exact same profile digest hai stage hoặc explicit abort/restart; preserve history.
- **AA-08 / High:** Một kiến trúc từng được Human duyệt có thể bị Brain tự retire do cost. **Giải:** Trigger chỉ yêu cầu re-evaluation; Human risk/priority/Kernel authority không bị ghi đè.

### 12.2. Audit ban đầu của A — Vòng 2 ADVERSARIAL_AUDIT_AND_RECONCILE (historical desk-check)

- **Test T1 — TASK-334:** Broker mới được mô tả là “cần để chứng minh source”. Overlay F-A/F-D buộc so sánh existing Runtime boundary với independent issuer, xác định threat requirement và operation cost trước khi mở thêm prerequisite; **DESK_POLICY_COVERED (theo thiết kế)**.
- **T2 — TASK-329:** Model ưu tiên latency và muốn bỏ Kernel gate. F-B + hard floors không cho phép; **DESK_POLICY_COVERED**.
- **T3 — TASK-304:** Một lỗi socket cụ thể, đầy đủ reproducible evidence. F-F/F-E vẫn giữ 2 stages nhưng không ép full RA; **DESK_POLICY_COVERED**.
- **T4 — TASK-320:** Model muốn bỏ base proof bằng file-name heuristic. F-C/F-G không cho false PASS, yêu cầu exact lawful comparability; **DESK_POLICY_COVERED**.
- **T5 — TASK-336:** Muốn hard reset `main` thay vì forward recovery. F-H + authority floors cấm rewrite history; **DESK_POLICY_COVERED**.
- **T6 — Mixed domain:** Một TASK “minor hardening” có embedded new credential issuer. Stage 2 phải phát hiện F-D+F-A; không được coi local; **DESK_POLICY_COVERED (theo thiết kế)**.
- **T7 — Stale main/profile:** Stage 1 fingerprint không còn hợp lệ. Rebuild/restart, không merge stages; **DESK_POLICY_COVERED**.
- **T8 — Unknown baseline:** Không được kết luận measured ROI chỉ từ pytest PASS hoặc Git LOC; **DESK_POLICY_COVERED**.
- **T9 — Mandatory high threat model:** Simpler alternative không đáp ứng Human hard requirement. Loại alternative, không ép security downgrade; **DESK_POLICY_COVERED**.
- **T10 — Additional complexity:** Proposed V1 yêu cầu thêm router DB/authority. Vi phạm non-goals, **REJECT candidate**.

**Đọc đúng kết quả A lịch sử:** Những nhãn `DESK_POLICY_COVERED` chỉ cho biết bản quy tắc A có lời giải ở mức thiết kế. Không phải một lần chạy V3/V1, không phải independent production conformance, không chứng minh VP-03 thực tế sẽ bị ngăn, và không phải Runtime/Reviewer PASS.

### 12.3. A+ reconciled findings (bốn bổ sung từ audit độc lập)

| ID | Severity trong thiết kế | Phát hiện | Disposition trong A+ | Chứng cứ/việc chưa hoàn thành |
|---|---|---|---|---|
| **F01** | Critical migration regression risk | `policy.version == 3` bật cả conformance và private metadata guards | **ADDRESSED_BY_RECONCILIATION** tại §10.1/§10.2, C01/C04/C06; retain parity bắt buộc | Chưa chạy negative implementation tests; cutover **NOT READY** |
| **F02** | High | Transient Stage-1 focus notes không nằm trong `construct_fingerprint` | **ADDRESSED_BY_RECONCILIATION** tại §3/§4.3/§5 S2.2; Stage 2 independently challenges exact bound candidate | Chưa có live provider evidence về substantive focus detection |
| **F03** | High | Bản A chưa so sánh việc nâng chính V1 với giữ V3 | **ADDRESSED_BY_RECONCILIATION** tại §6.0/C07; phương án A remains valid | Utility B over A **UNKNOWN** |
| **F04** | High | Desk-check được gọi PASS như measured verification | **ADDRESSED_BY_RECONCILIATION** tại §11.3A/§12.2; tách outcome labels, paired evaluation | Chưa có paired measured results; superiority **UNKNOWN** |

Các disposition trên chỉ nói **ngôn ngữ đặc tả đã xử lý rủi ro thiết kế**, không phải chứng minh implementation đã khắc phục bằng Runtime verification.

### 12.4. Final closure sweep trên chính văn bản A+ (desk-level)

| Eight mandatory V3 lens | Challenge trên reconciled A+ | Design closure | Điều kiện ngoài phạm vi draft |
|---|---|---|---|
| `AUTHORITY_BOUNDARY` | Không có Audit Router, independent Reviewer, score authority hay Human risk override | **CLEAR_DESIGN** | Review/publish/Runtime tách quyền khi triển khai |
| `SCOPE_NON_GOALS` | Giữ V3 là phương án hợp lệ; không thêm DB, new stage hoặc production edits | **CLEAR_DESIGN** | Không được expand scope ở TASK tương lai |
| `PROVENANCE_LINEAGE` | Packet/profile/construct exact binding; notes ngoài fingerprint không là truth | **CLEAR_DESIGN** | Cross-profile/stale SHA negative tests |
| `FAILURE_MODE_COUNTEREXAMPLES` | Chặn V3 private metadata guard regression, hybrid wrong focus, false self-PASS | **CLEAR_DESIGN** | Execute negative conformance test matrix |
| `AC_CONSISTENCY_COMPLETENESS` | C01–C20 có parity, paired protocol, measured unknown và explicit activation | **CLEAR_DESIGN** | Satisfy AC with valid implementation/evidence later |
| `VERIFICATION_OWNERSHIP_ORDERING` | Human/Brain audit outputs không tự biến thành Runtime RESULT/Reviewer PASS | **CLEAR_DESIGN** | Exact canonical verification remains Runtime-owned |
| `PORTABILITY_PRIVACY_BOUNDEDNESS` | Digest-bound bounded procedural profile; reserved/private path/CoT constraints | **CLEAR_DESIGN** | Actual V1 profile/validator/provider parity untested |
| `SIMPLIFICATION_DUPLICATE_AUTHORITY` | Minimal successor B conditional; C rejected; no extra audit infrastructure | **CLEAR_DESIGN** | Compare B with retain-V3 A before claimed ROI |

**Desk-level outcome:** `A_PLUS_DRAFT_CANDIDATE_FOR_HUMAN_DESIGN_REVIEW`. Đây không phải BP-4A serialized protocol verdict, không phải independent Reviewer PASS. Không thấy blocker ngữ nghĩa trong nội dung A+ sau reconciliation; **implementation readiness = NOT_PROVED**, **North-Star measured improvement = UNKNOWN**, **prospective cutover = NOT_AUTHORIZED**. Không tạo một audit recursion/vòng 3 từ kết quả này.

## 13. Human-approved prospective roadmap priority and implementation boundary

**Explicit Human decision — 2026-10-12:** Chấp thuận hướng **B — nâng cấp tối thiểu từ High Value V3 thành AIOS_ADAPTIVE_BRAIN_AUDIT_V1 (A+)** và ưu tiên triển khai ngay ở bước authoring/planning. Quyết định này ưu tiên công việc Adaptive Audit trước VP03 Runtime Lean Validity; không phủ nhận các kết quả VP03 đã review/publish, không tự kích hoạt profile, không thay đổi frozen Kernel/Constitution.

**NEXT planning commitment:** `adaptive-brain-audit-v1-minimal-successor-task-authoring`. Human/Brain author một TASK implementation tối thiểu từ exact fresh main, sau khi lập impact map và audit hai vòng theo current valid authoring semantics; chưa gán TASK ID, chưa có admitted Executor hoặc RUN. Trước khi dispatch, cần explicit Human delegation cho Executor/model/effort, canonical TASK revision và Runtime admission. Trạng thái VP03 Lean trở thành queued/paused; chỉ tiếp tục sau khi Adaptive Audit được reconciled closure hoặc Human đổi ưu tiên. Không auto-advance roadmap.

**Implementation approach:** Giữ một active semantic audit profile, hai stage, tám mandatory lens, profile digest/version binding, RA có điều kiện; không thêm router, state store, authority. Audit ảnh hưởng đến registry/profile parsing, V2/V3 historical compatibility, `brain_audit.py`, `brain_provider_protocol.py`, conformance negative tests, existing Brain authoring/BO-6 boundaries. Không ghép Stage 1 V3 với Stage 2 V1, không suy luận focus bằng score cố định. Migration F01–F04 và C01–C20 của tài liệu này bắt buộc được bảo toàn trong TASK.

**Activation guard:** Mọi thay đổi source cần Runtime verification, Reviewer semantic verdict và Publisher lawful publication; chuyển active profile chỉ sau reviewed/published proof + Human/Brain prospective cutover reconciliation. Paired V3/V1 performance/quality evidence `UNKNOWN` cho đến khi đo thực. Không dùng planning document, GitHub PR hoặc source commit như RESULT/REVIEW/PUBLISH proof.

**Branch safety:** Author TASK từ *chính canonical main mới* sau khi planning change thực sự có trên main; bind expected-main SHA/CAS; source branch là descendant của main, không dùng stale planning/repair branch, không reset/force-push; publication bằng Publisher đối với exact eligible reviewed source. Nếu main di chuyển hoặc có planning conflict, re-sync/reconcile trước mutation. Các file này có thể được chuẩn bị trên nhánh riêng để review, nhưng bản trên nhánh/PR chưa phải roadmap canonical cho đến khi merge hợp lệ.

**Known retrospective reconciliation:** Original design matrix TASK-302..336 là snapshot lịch sử, không phải latest-30 theo main mới. TASK-336 đã có historical failures; TASK-339 is the separately reconciled forward-recovery item on main at planning base, with published source SHA `1119a603f84e498d0169a5b216ce09f005924213` and main planning reconciliation `c205acbfb76ed843580d6d97c9d1309a095a5e52`. Keep exact failure/repair/review lineage immutable.

---

## 14. Tài liệu canonical đã đối chiếu (read-only, tại anchor SHA trên)

- `docs/AIOS-MANIFESTO.md` — North Star.
- `docs/AIOS-CONSTITUTION.md` — 10 constitutional principles, Human authority.
- `docs/CHATGPT_PROJECT_CONTRACT.md` — Brain behavior, §11 TASK Design Audit, BO-1 ingress retirement.
- `docs/AIOS-RENEW-KERNEL-v0.1-SPEC.md` và freeze — legacy/intra-PRIMARY semantic boundary.
- `docs/AIOS-BRAIN-SEMANTIC-AUDIT-PROTOCOL.md` — BP-4A two-stage/lens/fingerprint/final closure.
- `.ai/brain-audit-profiles.yaml` — published `brain-high-value-v3` và `v2` profile.
- `.ai/research-audit-profiles.yaml` / `src/aios_renew/research_contract.py` — `research-high-value-v1`, research supports.
- `src/aios_renew/brain_audit.py`, `src/aios_renew/brain_provider_protocol.py`, `tests/test_brain_audit.py` — V3 compatibility seams.
- `docs/AIOS-BRAIN-PORTABILITY.md`, `docs/AIOS-BRAIN-OPTIMIZATION-ROADMAP-v1.md` — BO-6 / BO-9B / BO-10 and provider portability.
- `.ai/roadmap-state.yaml` — planning state only.
- `.ai/tasks/TASK-{302..309,313..322,325..336}.yaml` — đúng 30 TASK authoring contracts.
- So sánh Git `84478a59405427ef932740eb26d8ce6292b0fe71...e951bd3fd896bdd5ad4082f8bc9b35a26b23f1f4` — VP03 footprint (29 commits, 31 file changes, 6 modules mới). Không dùng số liệu này như đo hiệu quả.

### A+ lineage & provenance (read-only)

- Original A: `AIOS_ADAPTIVE_BRAIN_AUDIT_V1_SPEC_DRAFT_2026-10-10.md` — baseline nội dung, 385 dòng, 30 TASK matrix.
- Independent B: `AIOS_ADAPTIVE_BRAIN_AUDIT_V1_INDEPENDENT_RECONSTRUCTION_2026-10-11.md` — audit hai vòng trên canonical main theo phạm vi đã khai báo.
- Comparison: `AIOS_ADAPTIVE_BRAIN_AUDIT_V1_INDEPENDENT_COMPARISON_2026-10-11.md` — giải thích 14 findings không phải 14 lỗ hổng mới của A, xác định F01–F04.
- Unified A+: hồ sơ thiết kế được Human duyệt ở mức planning. Khi tài liệu và roadmap được merge hợp lệ vào canonical main, đây là architecture planning baseline; vẫn chưa tạo active profile hay chứng minh conformance.

**END — HUMAN-APPROVED PROSPECTIVE PLANNING SPECIFICATION; IMPLEMENTATION/CUTOVER NOT AUTHORIZED BY DOCUMENT ALONE**