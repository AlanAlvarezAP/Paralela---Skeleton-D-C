#ifndef HEART_BEAT_H
#define HEART_BEAT_H

#include <deque>
#include <utility>
#include <chrono>
#include "dparallel_recursion/seq_parallel_stack_recursion.h"



namespace dpr
{
    namespace internal_mpi
    {
        const long long HB_DEFAULT_PERIOD  = 30;
        const unsigned  HB_DEFAULT_CHECK_EVERY = 128;

        template<typename T> struct StealStack;

        template <typename TYPE>
        class PrivateStack
        {
        public:
            PrivateStack():
                data()
            {}
        
            void push(TYPE&& in_value)
            {
                data.push_back(std::move(in_value));
            }
        
            TYPE pop()
            {
                TYPE x = std::move(data.back());
                data.pop_back();
        
                return x;
            }
        
            TYPE take_oldest()
            {
                TYPE x = std::move(data.front());
                data.pop_front();
        
                return x;
            }
        
            bool can_promote() { return data.size() >= 2; }
        
            bool empty() { return data.empty(); }
        private:
            std::deque<TYPE> data;
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
#ifndef DPR_DENY_STACK_RESIZE
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
                    sc.ws_make_progress(ss);
                }else{
                    SC::cbarrier_cancel();
                }
            }
        }

        template<class T, class SC>
        inline void hb_step(PrivateStack<T> &ps, SC &sc, StealStack<T> *ss, HBState &st){
            st.poll_counter++;
            if(sc.num_total_procs > 1 && st.poll_counter >= sc.polling_interval){
                st.poll_counter = 0;
                sc.ws_make_progress(ss);
            }

            if(hb_tick(st)){
                hb_promote(ps,sc,ss,st);
            }
        }

        template<class T, class Info>
        inline void push_children(PrivateStack<T>& in_stack, T& x, const Info& info)
        {
            const int n = info.num_children(x);
            for (int i = n - 1; i >= 0; --i)
                in_stack.push(info.child(i, x));
        }

        template<class Return, class T, class Info, class Body, class StackClass>
        unsigned long long heartbeat_serial(Return& r, T parent, const Info& info,
                                            Body& body, StackClass& sc, StealStack<T>* ss)
        {
            PrivateStack<T> stack;
            HBState &hb_state = hb_thread_state();
            unsigned long long nodes = 0;

            push_children(stack, parent, info);

            while (!stack.empty())
            {
                T x = stack.pop();

                body.pre(x);
                nodes++;

                if (info.is_base(x))
                    body.post(body.base(x), r);
                else
                {
                    body.pre_rec(x);
                    if (body.processNonBase)
                        body.post(body.non_base(x), r);

                    push_children(stack, x, info);
                }

                hb_step(stack, sc, ss, hb_state);
            }

            return nodes;
        }

        template<class T, class Info, class Body, class StackClass>
        unsigned long long heartbeat_serial(T parent, const Info& info,
                                            Body& body, StackClass& sc, StealStack<T>* ss)
        {
            PrivateStack<T> stack;
            HBState &hb_state = hb_thread_state();
            unsigned long long nodes = 0;

            push_children(stack, parent, info);

            while (!stack.empty())
            {
                T x = stack.pop();

                body.pre(x);
                nodes++;

                if (info.is_base(x))
                    body.base(x);
                else
                {
                    body.pre_rec(x);
                    if (body.processNonBase)
                        body.non_base(x);
                    push_children(stack, x, info);
                }

                hb_step(stack, sc, ss, hb_state);
            }

            return nodes;
        }

        
    }
}

#endif