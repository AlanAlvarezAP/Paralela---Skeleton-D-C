#ifndef HEART_BEAT_H
#define HEART_BEAT_H

#include <deque>
#include <utility>
#include "dparallel_recursion/seq_parallel_stack_recursion.h"


namespace dpr
{
    namespace internal_mpi
    {
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
        
        class HBState // Esto no se si deberías hacerlo clase o struct tu decidelo
        {
            
        };

        // Igual aca deberían ir, hb_tick y hb_promote

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
            HBState hb_state;
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

                //if (hb_state.hb_tick(st)) // 
                //    hb_state.hb_promote(pila, sc, ss, hb_state);
            }

            return nodes;
        }

        template<class T, class Info, class Body, class StackClass>
        unsigned long long heartbeat_serial(T parent, const Info& info,
                                            Body& body, StackClass& sc, StealStack<T>* ss)
        {
            PrivateStack<T> stack;
            HBState hb_state;
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

                // if (hb_state.hb_tick(hb_state))
                //     hb_state.hb_promote(stack, sc, ss, hb_state);
            }

            return nodes;
        }

        
    }
}

#endif