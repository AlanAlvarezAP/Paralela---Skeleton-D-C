#ifndef HEART_BEAT_H
#define HEART_BEAT_H

#include <deque>
#include <algorithm>
#include <vector>
#include <utility>
#include <chrono>
#include <type_traits>
#include "dparallel_recursion/seq_parallel_stack_recursion.h"



namespace dpr
{
    namespace internal_mpi
    {
        const long long HB_DEFAULT_PERIOD  = 30;
        const unsigned  HB_DEFAULT_CHECK_EVERY = 128;

        template<typename T> struct StealStack;

        // true if Info has do_serial_func
        template<class Info, class T, class Return>
        struct hb_has_do_serial_func {
            template<class I>
            static auto test(int) -> decltype((void)std::declval<const I*>()->do_serial_func(std::declval<T&>(), std::declval<Return&>()), std::true_type());
            template<class I>
            static std::false_type test(...);
            typedef decltype(test<Info>(0)) type;
            static const bool value = type::value;
        };

        template<class Info, class T>
        struct hb_has_do_serial_func_void {
            template<class I>
            static auto test(int) -> decltype((void)std::declval<const I*>()->do_serial_func(std::declval<T&>()), std::true_type());
            template<class I>
            static std::false_type test(...);
            typedef decltype(test<Info>(0)) type;
            static const bool value = type::value;
        };

        template <typename TYPE>
        class PrivateStack
        {
        public:
            PrivateStack(): data(), bottom(0) { data.reserve(256); }

            void push(TYPE&& v)
            {
                data.push_back(std::move(v));
            }
            void push(const TYPE& v)
            {
                data.push_back(v);
            }

            TYPE pop()
            {
                TYPE x = std::move(data.back());
                data.pop_back();
                if (data.size() == bottom)
                {
                    data.clear();
                    bottom = 0;
                }
                return x;
            }

            TYPE take_oldest()
            {
                TYPE x = std::move(data[bottom++]);
                if (bottom >= 64 && bottom * 2 >= data.size())
                {
                    data.erase(data.begin(), data.begin() + bottom);
                    bottom = 0;
                }
                return x;
            }

            bool can_promote() const { return data.size() - bottom >= 2; }
            bool empty() const { return data.size() == bottom; }
            std::size_t size() const { return data.size() - bottom; }
        private:
            std::vector<TYPE> data;
            std::size_t bottom;
        };
        
        struct HBConfig{
            std::chrono::steady_clock::duration period;
            unsigned int check_every;

            HBConfig(): period(std::chrono::microseconds(HB_DEFAULT_PERIOD)),check_every(HB_DEFAULT_CHECK_EVERY){};
        };

        inline HBConfig& hb_config(){
            static thread_local HBConfig c;
            return c;
        }

        class HBState{
        public:
            std::chrono::steady_clock::time_point t0;
            unsigned int counter;
            unsigned int poll_counter;
            unsigned long long promotions;

            HBState():t0(std::chrono::steady_clock::now()),counter(0),poll_counter(0),promotions(0){};
        };

        inline HBState& hb_thread_state(){
            static thread_local HBState s;
            return s;
        }

        inline bool hb_tick(HBState& st){
            st.counter++;
            if(st.counter < hb_config().check_every){
                return false;
            }
            st.counter = 0;
            return (std::chrono::steady_clock::now() - st.t0) >= hb_config().period;
        }

        template<class T, class SC>
        void hb_promote(PrivateStack<T> & ps, SC& sc, StealStack<T>* ss, HBState &st){
            if(!ps.can_promote()){
                return;
            }

            int needed = sc.chunkSize - sc.ss_localDepth(ss);
            if(needed < 1){
                needed = 1;
            }

            for(int k = 0; k<needed && ps.can_promote(); k++){
#ifdef DPR_DENY_STACK_RESIZE
                if(ss->top + 1 > ss->stackSize){
                    break;
                }
#else
                while(ss->top + 1 > ss->stackSize){
                    sc.ss_growStackSize(ss);
                }
#endif
                sc.ss_push_simple(ss,ps.take_oldest());
                st.promotions++;
            }

            st.t0 = std::chrono::steady_clock::now();
            if(sc.ss_localDepth(ss) >= sc.chunkSize){
                sc.ss_release(ss,sc.chunkSize * (sc.ss_localDepth(ss) / sc.chunkSize));
                if(sc.num_total_procs > 1){
#ifndef DPR_USE_REGULAR_POLLING_INTERVAL
                    if(ss->polling_count % sc.polling_interval == 0){
                        sc.ws_make_progress(ss);
                    }
                    ss->polling_count++;
#endif
                }else{
                    SC::cbarrier_cancel();
                }
            }
        }

        template<class T, class SC>
        inline void hb_step(PrivateStack<T> &ps, SC &sc, StealStack<T> *ss, HBState &st)
        {
            if(sc.num_total_procs > 1 && ++st.poll_counter >= std::max<unsigned>(sc.polling_interval, hb_config().check_every)){
                st.poll_counter = 0;
                sc.ws_make_progress(ss);
            }

            if(hb_tick(st)){
                hb_promote(ps,sc,ss,st);
            }
        }

        // GatherInput needs a copy of the processed nodes
        struct hb_no_copy_input {
            template<class SC, class T>
            static void copy(SC&, StealStack<T>*, const T&) {}
        };

        struct hb_copy_input {
            template<class SC, class T>
            static void copy(SC& sc, StealStack<T>* ss, const T& x) { sc.ss_push_copy(ss, x); }
        };

        template<class CopyInput, class T, class Info, class Body, class SC, class LeafFn>
        inline unsigned long long hb_expand(PrivateStack<T>& stack, T& px, const Info& info,
                                            Body& body, SC& sc, StealStack<T>* ss, LeafFn on_leaf)
        {
            unsigned long long leaves = 0;
            const int nc = info.num_children(px);
#ifdef DPR_FORCE_ORDER_R_TO_L
            for (int i = 0; i < nc; ++i)
#else
            for (int i = nc - 1; i >= 0; --i)
#endif
            {
                T c = info.child(i, px);
                body.pre(c);
                if (info.is_base(c))
                {
                    leaves++;
                    on_leaf(c);
                    CopyInput::copy(sc, ss, c);
                }
                else
                    stack.push(std::move(c));
            }
            return leaves;
        }

        template<class Return, class T, class Info, class Body, class StackClass, class CopyInput = hb_no_copy_input>
        unsigned long long heartbeat_serial(Return& r, T parent, const Info& info,
                                            Body& body, StackClass& sc, StealStack<T>* ss,
                                            CopyInput = CopyInput())
        {
            PrivateStack<T> stack;
            HBState &hb_state = hb_thread_state();
            auto on_leaf = [&](T& c) { body.post(body.base(c), r); };

            unsigned long long nodes = hb_expand<CopyInput>(stack, parent, info, body, sc, ss, on_leaf);

            while (!stack.empty())
            {
                T x = stack.pop();
                nodes++;
                body.pre_rec(x);
                if (body.processNonBase)
                {
                    body.post(body.non_base(x), r);
                    CopyInput::copy(sc, ss, x);
                }
                nodes += hb_expand<CopyInput>(stack, x, info, body, sc, ss, on_leaf);
                hb_step(stack, sc, ss, hb_state);
            }

            return nodes;
        }

        template<class T, class Info, class Body, class StackClass, class CopyInput = hb_no_copy_input>
        unsigned long long heartbeat_serial(T parent, const Info& info,
                                            Body& body, StackClass& sc, StealStack<T>* ss,
                                            CopyInput = CopyInput())
        {
            PrivateStack<T> stack;
            HBState &hb_state = hb_thread_state();
            auto on_leaf = [&](T& c) { body.base(c); };

            unsigned long long nodes = hb_expand<CopyInput>(stack, parent, info, body, sc, ss, on_leaf);

            while (!stack.empty())
            {
                T x = stack.pop();
                nodes++;
                body.pre_rec(x);
                if (body.processNonBase)
                {
                    body.non_base(x);
                    CopyInput::copy(sc, ss, x);
                }
                nodes += hb_expand<CopyInput>(stack, x, info, body, sc, ss, on_leaf);
                hb_step(stack, sc, ss, hb_state);
            }

            return nodes;
        }

        
    }
}

#endif